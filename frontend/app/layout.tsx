import type { Metadata, Viewport } from "next";
import NavBar from "@/components/NavBar";
import { APP_NAME, APP_TAGLINE } from "@/lib/branding";
import "./globals.css";

export const metadata: Metadata = {
  title: APP_NAME,
  description: APP_TAGLINE,
};

// Explicit so phones lay out at device width instead of a ~980px desktop viewport.
// `maximumScale` is deliberately left unset: pinch-zoom stays available, which matters
// for the dense option-chain / stocks grids on a small screen.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">
        <NavBar />
        {/*
          Progressive width cap. A flat max-w-[1600px] left ~800px of dead space on each
          side of a 3200px display; these arbitrary min-[…] breakpoints let the layout
          keep growing on large monitors while still capping line length on a normal one.
        */}
        <main className="mx-auto max-w-[1600px] px-3 py-3 sm:px-4 sm:py-4 min-[1920px]:max-w-[1800px] min-[2400px]:max-w-[2200px] min-[3000px]:max-w-[2600px]">
          {children}
        </main>
      </body>
    </html>
  );
}
