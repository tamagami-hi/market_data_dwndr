"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import SessionBadge from "@/components/SessionBadge";
import { NotificationCenter } from "@/components/operator-events/NotificationCenter";
import { APP_NAME } from "@/lib/branding";

const LINKS = [
  { href: "/monitor", label: "Capture Monitor", mobileLabel: "Monitor" },
  { href: "/option-chain", label: "Option Chain", mobileLabel: "Options" },
  { href: "/stocks", label: "Stocks", mobileLabel: "Stocks" },
  { href: "/login", label: "Downloader", mobileLabel: "Setup" },
];

export default function NavBar() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Primary"
      className="sticky top-0 z-40 border-b border-border bg-canvas"
    >
      <div className="workspace-shell flex h-12 items-center justify-between gap-2 px-3 sm:h-14 sm:px-4">
        <Link
          href="/"
          className="flex min-h-11 shrink-0 items-center gap-2 text-sm font-semibold text-primary sm:mr-4"
        >
          <span
            aria-hidden="true"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-accent/60 bg-surface-2 font-mono text-[10px] text-accent"
          >
            TV
          </span>
          {APP_NAME}
        </Link>
        <div className="ml-auto flex h-full min-w-0 items-center gap-1">
          <div className="hidden h-full items-center gap-1 sm:flex">
            {LINKS.map((link) => {
              const active = pathname === link.href;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`control inline-flex min-h-10 items-center whitespace-nowrap px-3 text-sm ${
                    active
                      ? "border-border bg-surface-2 text-accent"
                      : "text-secondary hover:bg-surface-1 hover:text-primary"
                  }`}
                >
                  {link.label}
                </Link>
              );
            })}
          </div>
          <NotificationCenter />
          <span className="ml-1 shrink-0 sm:ml-2">
            <SessionBadge />
          </span>
        </div>
      </div>
      <div className="grid h-12 grid-cols-4 border-t border-border sm:hidden">
        {LINKS.map((link) => {
          const active = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              aria-label={link.label}
              aria-current={active ? "page" : undefined}
              className={`grid min-h-11 min-w-0 place-items-center border-r border-border px-1 text-xs last:border-r-0 ${
                active ? "bg-surface-2 font-semibold text-accent" : "text-secondary"
              }`}
            >
              {link.mobileLabel}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
