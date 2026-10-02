from fastapi.testclient import TestClient

import griot_analyzer.agent as agent_mod
from griot_analyzer.agent import Agent, create_agent_app


def make_client(tmp_path, monkeypatch):
    web = tmp_path / "out"
    (web / "about").mkdir(parents=True)
    (web / "index.html").write_text("<h1>studio</h1>")
    (web / "about.html").write_text("<h1>about</h1>")
    (web / "404.html").write_text("missing")
    (tmp_path / "secret.txt").write_text("nope")
    monkeypatch.setattr(agent_mod, "WEB_OUT", web)
    return TestClient(create_agent_app(Agent(tmp_path / "home", "http://127.0.0.1:9", "t")))


def test_status_reports_desktop_and_idle(tmp_path, monkeypatch):
    s = make_client(tmp_path, monkeypatch).get("/agent/status").json()
    assert s["desktop"] is True and s["job"]["kind"] == "idle"


def test_scan_rejects_missing_folder(tmp_path, monkeypatch):
    r = make_client(tmp_path, monkeypatch).post("/agent/scan", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_serves_exported_pages_and_blocks_traversal(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    assert "studio" in c.get("/").text
    assert "about" in c.get("/about").text  # directory exists, falls through to about.html
    assert c.get("/does-not-exist").status_code == 404
    assert c.get("/..%2Fsecret.txt").status_code == 404
