import Link from "next/link";

import { APP_NAME, APP_TAGLINE } from "@/lib/branding";

const CARDS = [
  {
    href: "/monitor",
    title: "Capture Monitor",
    body: "Per-underlying WS health, frames written, file size, 1 Hz heartbeat, and MARKET_DATA disk usage — live.",
  },
  {
    href: "/option-chain",
    title: "Option Chain",
    body: "ATM ± 50 index chains with reconstructed IV & Greeks, spot / ATM / max-pain markers, live keyframes + deltas.",
  },
  {
    href: "/stocks",
    title: "Stocks Board",
    body: "F&O stock matrix: spot + up to 3 nearest futures with live and daily calendar spreads.",
  },
];

export default function Home() {
  return (
    <div className="py-6">
      <h1 className="text-2xl font-semibold text-zinc-100">{APP_NAME}</h1>
      <p className="mt-1 text-sm text-zinc-400">{APP_TAGLINE}</p>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {CARDS.map((card) => (
          <Link
            key={card.href}
            href={card.href}
            className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 transition-colors hover:border-sky-500/40 hover:bg-zinc-900"
          >
            <h2 className="text-lg font-semibold text-zinc-100">{card.title}</h2>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">{card.body}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}
