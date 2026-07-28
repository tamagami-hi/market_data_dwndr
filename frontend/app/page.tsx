import Link from "next/link";

import { PageFrame } from "@/components/ui/PageFrame";
import { PageHeader } from "@/components/ui/PageHeader";
import { APP_NAME, APP_TAGLINE } from "@/lib/branding";

const DESTINATIONS = [
  {
    href: "/monitor",
    title: "Capture Monitor",
    body: "Session context, capture health, data loss, stream freshness, storage, compression, and logs.",
    className: "sm:col-span-2 lg:col-span-12",
    primary: true,
    meta: "Primary operator surface",
  },
  {
    href: "/option-chain",
    title: "Option Chain",
    body: "Complete call, strike, and put matrix with price, flow, and reconstructed Greeks.",
    className: "lg:col-span-4",
    primary: false,
    meta: "Live index derivatives",
  },
  {
    href: "/stocks",
    title: "Stocks Board",
    body: "Spot and futures matrix with live spreads, scalars, and four L1-L5 order books.",
    className: "lg:col-span-4",
    primary: false,
    meta: "Live F&O depth",
  },
  {
    href: "/login",
    title: "Downloader",
    body: "Review token automation, market phase, prerequisites, and capture readiness.",
    className: "sm:col-span-2 lg:col-span-4",
    primary: false,
    meta: "Setup and service status",
  },
];

export default function Home() {
  return (
    <PageFrame>
      <PageHeader title={APP_NAME} description={APP_TAGLINE} eyebrow="Operator workstation" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-12">
        {DESTINATIONS.map((destination) => (
          <Link
            key={destination.href}
            href={destination.href}
            className={`panel control group flex min-h-36 flex-col justify-between p-4 hover:border-border-strong hover:bg-surface-2 ${destination.className} ${
              destination.primary ? "min-h-48 border-accent/40 bg-surface-2 lg:p-6" : ""
            }`}
          >
            <div>
              <p className="label text-muted">{destination.meta}</p>
              <h2 className={`${destination.primary ? "mt-4 text-2xl" : "mt-3 text-lg"} font-semibold tracking-tight text-primary`}>
                {destination.title}
              </h2>
              <p className="mt-2 max-w-xl text-sm leading-relaxed text-secondary">{destination.body}</p>
            </div>
            <span className="mt-6 text-sm font-semibold text-accent group-hover:text-primary">Open {destination.title}</span>
          </Link>
        ))}
      </div>
    </PageFrame>
  );
}
