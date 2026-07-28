import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Stocks",
  description: "Live TickVault stock and futures board with complete L1-L5 depth.",
};

export default function StocksLayout({ children }: { children: ReactNode }) {
  return children;
}
