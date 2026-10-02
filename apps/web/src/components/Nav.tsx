"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useDesktop } from "@/lib/agent";

const LINKS = [
  { href: "/", label: "Studio" },
  { href: "/library", label: "Library" },
  { href: "/about", label: "How it works" },
];

export function Nav() {
  const path = usePathname();
  const desktop = useDesktop();
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-page/85 backdrop-blur">
      <nav className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3 sm:px-6">
        <Link href="/" className="text-base font-semibold tracking-tight">
          Griot
        </Link>
        <div className="flex gap-1 text-sm">
          {LINKS.map((l) => {
            const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`rounded-md px-2.5 py-1 ${active ? "bg-surface font-medium text-ink shadow-sm" : "text-ink-2 hover:text-ink"}`}
              >
                {l.label}
              </Link>
            );
          })}
        </div>
        <span className="ml-auto text-xs text-muted">{desktop ? "Desktop app" : "Web"}</span>
      </nav>
    </header>
  );
}
