"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useDesktop } from "@/lib/agent";

const LINKS = [
  { href: "/", label: "Studio" },
  { href: "/library", label: "Library" },
  { href: "/about", label: "How it works" },
];

function ThemeToggle() {
  const [light, setLight] = useState(false);
  useEffect(() => {
    const h = setTimeout(() => setLight(document.documentElement.dataset.theme === "light"), 0);
    return () => clearTimeout(h);
  }, []);
  const flip = () => {
    const next = !light;
    setLight(next);
    if (next) document.documentElement.dataset.theme = "light";
    else delete document.documentElement.dataset.theme;
    try {
      localStorage.setItem("griot.theme", next ? "light" : "dark");
    } catch {}
  };
  return (
    <button
      onClick={flip}
      aria-label={light ? "Switch to dark theme" : "Switch to light theme"}
      className="flex size-8 items-center justify-center rounded-full text-ink-2 hover:bg-surface-2 hover:text-ink"
    >
      {light ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      )}
    </button>
  );
}

/** Tiny animated equaliser used as the wordmark's companion. */
function Equalizer() {
  return (
    <span aria-hidden className="flex h-4 items-end gap-[2px]">
      {[0.2, 0.6, 0.35, 0.8, 0.5].map((d, i) => (
        <span
          key={i}
          className="eq-bar w-[3px] rounded-sm"
          style={{ height: "100%", animationDelay: `${-d}s`, background: i % 2 ? "var(--gold)" : "var(--accent-text)" }}
        />
      ))}
    </span>
  );
}

export function Nav() {
  const path = usePathname();
  const desktop = useDesktop();
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-page/80 backdrop-blur-md">
      <div className="kente h-[3px] opacity-80" />
      <nav className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3 sm:gap-6 sm:px-6">
        <Link href="/" className="flex items-center gap-2">
          <Equalizer />
          <span className="hidden font-display text-xl font-semibold tracking-tight min-[420px]:inline">Griot</span>
        </Link>
        <div className="flex gap-0.5 text-sm sm:gap-1">
          {LINKS.map((l) => {
            const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`whitespace-nowrap rounded-full px-2.5 py-1 transition-colors sm:px-3 ${
                  active ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:text-ink"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </div>
        <span className="ml-auto hidden font-mono text-[11px] uppercase tracking-widest text-muted sm:inline">
          {desktop ? "desktop" : "web"}
        </span>
        <ThemeToggle />
      </nav>
    </header>
  );
}
