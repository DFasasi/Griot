//! Griot desktop shell.
//!
//! The app itself is the shared web UI plus the Python analyzer agent, both served by the
//! agent on http://127.0.0.1:51735. This shell only:
//!   1. shows a splash page,
//!   2. starts the agent (or reuses one that is already running),
//!   3. points the window at the agent once it answers,
//!   4. stops the agent it started when the app quits.

use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

const PORT: u16 = 51735;

struct AgentProcess(Mutex<Option<Child>>);

fn agent_up() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], PORT));
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// Dev: run the agent from the repo with uv. A packaged build sets GRIOT_AGENT_CMD to the
/// bundled agent executable instead.
fn spawn_agent() -> std::io::Result<Child> {
    let repo: PathBuf = std::env::var("GRIOT_REPO")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."));
    // GUI apps on macOS don't inherit the shell PATH; add the usual tool locations.
    let path = format!(
        "{}:/opt/homebrew/bin:/usr/local/bin:{}/.local/bin:{}/.cargo/bin",
        std::env::var("PATH").unwrap_or_default(),
        std::env::var("HOME").unwrap_or_default(),
        std::env::var("HOME").unwrap_or_default(),
    );
    let mut cmd = match std::env::var("GRIOT_AGENT_CMD") {
        Ok(bin) => Command::new(bin),
        Err(_) => {
            let mut c = Command::new("uv");
            c.args(["run", "--project"]).arg(&repo).args(["griot", "app"]);
            c
        }
    };
    cmd.args(["--no-browser", "--port", &PORT.to_string()])
        .env("PATH", path)
        // The agent exits by itself once this process is gone, even after a crash or
        // force-quit when the Exit handler below never runs.
        .env("GRIOT_PARENT_PID", std::process::id().to_string())
        .current_dir(&repo)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
}

pub fn run() {
    tauri::Builder::default()
        .manage(AgentProcess(Mutex::new(None)))
        .setup(|app| {
            if !agent_up() {
                let child = spawn_agent().map_err(|e| format!("could not start the Griot agent: {e}"))?;
                *app.state::<AgentProcess>().0.lock().unwrap() = Some(child);
            }
            let window = app.get_webview_window("main").expect("main window");
            std::thread::spawn(move || {
                let started = Instant::now();
                while !agent_up() {
                    if started.elapsed() > Duration::from_secs(180) {
                        let _ = window.eval(
                            "document.getElementById('msg').textContent = \
                             'The local analyzer did not start. Run `uv run griot app` to see why.'",
                        );
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(400));
                }
                let url = format!("http://127.0.0.1:{PORT}/").parse().expect("valid url");
                let _ = window.navigate(url);
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Griot")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<AgentProcess>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
