"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import SessionBadge from "@/components/SessionBadge";
import { APP_NAME } from "@/lib/branding";

const LINKS = [
  { href: "/monitor", label: "Capture Monitor" },
  { href: "/option-chain", label: "Option Chain" },
  { href: "/stocks", label: "Stocks" },
  { href: "/login", label: "Downloader" },
];

export default function NavBar() {
  const pathname = usePathname();
  return (
    // Mobile: two rows (brand + session badge, then a horizontally scrollable link
    // rail). From `sm` up it collapses to the original single row. The old layout was
    // one non-wrapping flex row of 6 items, which overflowed and overlapped on phones.
    <nav className="sticky top-0 z-40 flex flex-col gap-1 border-b border-zinc-800 bg-zinc-950/90 px-3 py-2 backdrop-blur sm:flex-row sm:items-center sm:gap-1 sm:px-4">
      <div className="flex items-center justify-between gap-2 sm:justify-start">
        <Link
          href="/"
          className="flex shrink-0 items-center gap-2 text-sm font-semibold text-zinc-100 sm:mr-4"
        >
          <span
            aria-hidden="true"
            className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-gradient-to-br from-sky-400 to-indigo-500"
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="#05121f" strokeWidth="2.4" strokeLinecap="round">
              <line x1="7" y1="15" x2="7" y2="19" /><line x1="12" y1="5" x2="12" y2="19" /><line x1="17" y1="11" x2="17" y2="19" />
            </svg>
          </span>
          {APP_NAME}
        </Link>
        {/* Badge sits beside the brand on mobile, and moves to the far right on sm+. */}
        <span className="sm:hidden">
          <SessionBadge />
        </span>
      </div>

      {/* Link rail: scrolls sideways on narrow screens instead of squeezing. */}
      <div className="-mx-1 flex gap-1 overflow-x-auto px-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden sm:mx-0 sm:overflow-visible sm:px-0">
        {LINKS.map((link) => {
          const active = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              aria-current={active ? "page" : undefined}
              className={`shrink-0 whitespace-nowrap rounded px-3 py-1.5 text-sm transition-colors ${
                active
                  ? "bg-sky-500/15 text-sky-300"
                  : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200"
              }`}
            >
              {link.label}
            </Link>
          );
        })}
      </div>

      <span className="ml-auto hidden sm:inline-flex">
        <SessionBadge />
      </span>
    </nav>
  );
}
