import type { Metadata } from "next";
import NavBar from "@/components/NavBar";
import { APP_NAME, APP_TAGLINE } from "@/lib/branding";
import "./globals.css";

export const metadata: Metadata = {
  title: APP_NAME,
  description: APP_TAGLINE,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">
        <NavBar />
        <main className="mx-auto max-w-[1600px] px-4 py-4">{children}</main>
      </body>
    </html>
  );
}
