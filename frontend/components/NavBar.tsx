"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import SessionBadge from "@/components/SessionBadge";

const LINKS = [
  { href: "/monitor", label: "Capture Monitor" },
  { href: "/option-chain", label: "Option Chain" },
  { href: "/stocks", label: "Stocks" },
  { href: "/login", label: "Login" },
];

export default function NavBar() {
  const pathname = usePathname();
  return (
    <nav className="flex items-center gap-1 border-b border-zinc-800 bg-zinc-950/80 px-4 py-2 backdrop-blur">
      <Link href="/" className="mr-4 text-sm font-semibold text-zinc-100">
        market_data_dwndr
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
