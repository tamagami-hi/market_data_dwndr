import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Downloader",
  description: "TickVault downloader prerequisites, automation, and capture readiness.",
};

export default function DownloaderLayout({ children }: { children: ReactNode }) {
  return children;
}
