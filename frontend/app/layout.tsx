import type { Metadata, Viewport } from "next";
import NavBar from "@/components/NavBar";
import { APP_NAME, APP_TAGLINE } from "@/lib/branding";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: APP_NAME,
    template: `%s | ${APP_NAME}`,
  },
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
        <a href="#main-content" className="skip-link">
          Skip to main content
        </a>
        <NavBar />
        <main id="main-content" tabIndex={-1} className="workspace-shell page-shell">
          {children}
        </main>
      </body>
    </html>
  );
}
