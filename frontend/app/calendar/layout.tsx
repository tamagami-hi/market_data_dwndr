import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Market Calendar",
  description: "TickVault holidays and special open days with their own market hours.",
};

export default function CalendarLayout({ children }: { children: ReactNode }) {
  return children;
}
