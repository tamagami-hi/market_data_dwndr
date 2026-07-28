import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Option Chain",
  description: "Live TickVault option chains with complete calls, puts, flow, and Greeks.",
};

export default function OptionChainLayout({ children }: { children: ReactNode }) {
  return children;
}
