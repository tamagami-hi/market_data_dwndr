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
    <nav className="flex items-center gap-1 border-b border-zinc-800 bg-zinc-950/80 px-4 py-2 backdrop-blur">
      <Link href="/" className="mr-4 flex items-center gap-2 text-sm font-semibold text-zinc-100">
        <span aria-hidden="true" className="grid h-6 w-6 place-items-center rounded-md bg-gradient-to-br from-sky-400 to-indigo-500">
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="#05121f" strokeWidth="2.4" strokeLinecap="round">
            <line x1="7" y1="15" x2="7" y2="19" /><line x1="12" y1="5" x2="12" y2="19" /><line x1="17" y1="11" x2="17" y2="19" />
          </svg>
        </span>
        {APP_NAME}
      </Link>
      {LINKS.map((link) => {
        const active = pathname === link.href;
        return (
          <Link
            key={link.href}
            href={link.href}
            className={`rounded px-3 py-1.5 text-sm transition-colors ${
              active
                ? "bg-sky-500/15 text-sky-300"
                : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
      <span className="ml-auto">
        <SessionBadge />
      </span>
    </nav>
  );
}
