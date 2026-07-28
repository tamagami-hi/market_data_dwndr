import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Capture Monitor",
  description: "Live TickVault capture health, data-loss, session, and storage telemetry.",
};

export default function MonitorLayout({ children }: { children: ReactNode }) {
  return children;
}
