/**
 * Product branding. The display name is env-driven and baked at build time via
 * NEXT_PUBLIC_APP_NAME (set in frontend/.env.local). Change the env value to
 * rebrand the whole UI — title, nav, and landing — without code changes.
 */
export const APP_NAME = process.env.NEXT_PUBLIC_APP_NAME?.trim() || "TickVault";

export const APP_TAGLINE =
  "Zerodha Kite market-data capture — live monitor and read-time reconstruction.";
