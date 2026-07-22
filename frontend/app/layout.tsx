import type { Metadata } from "next";
import NavBar from "@/components/NavBar";
import OperatorGate from "@/components/OperatorGate";
import "./globals.css";

export const metadata: Metadata = {
  title: "market_data_dwndr",
  description: "Zerodha Kite market-data capture monitor",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">
        <OperatorGate>
          <NavBar />
          <main className="mx-auto max-w-[1600px] px-4 py-4">{children}</main>
        </OperatorGate>
      </body>
    </html>
  );
}
