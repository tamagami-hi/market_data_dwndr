import type { CSSProperties } from "react";

/** Sessions losing more than this share of their scheduled seconds are highlighted. */
export const LOSS_HIGHLIGHT_MIN_PCT = 1;
/** At or above this loss the highlight is fully red. */
export const LOSS_FULL_SEVERITY_PCT = 20;

const MIN_TINT_PCT = 8;
const MAX_TINT_PCT = 26;

/**
 * 0..1 severity for a session's loss, or ``null`` when it should not be highlighted
 * (no recorded loss, or at most {@link LOSS_HIGHLIGHT_MIN_PCT}).
 */
export function lossSeverity(lossPct: number | null | undefined): number | null {
  if (typeof lossPct !== "number" || !Number.isFinite(lossPct)) return null;
  if (lossPct <= LOSS_HIGHLIGHT_MIN_PCT) return null;
  const span = LOSS_FULL_SEVERITY_PCT - LOSS_HIGHLIGHT_MIN_PCT;
  return Math.min(1, (lossPct - LOSS_HIGHLIGHT_MIN_PCT) / span);
}

/** Amber just above 1% loss, blending continuously to red at the full-severity loss. */
export function lossColor(severity: number): string {
  const redShare = Math.round(Math.max(0, Math.min(1, severity)) * 100);
  return `color-mix(in oklab, var(--danger) ${redShare}%, var(--warning))`;
}

/** Row style: a left bar in the loss colour and a background tint that deepens with it. */
export function lossRowStyle(lossPct: number | null | undefined): CSSProperties | undefined {
  const severity = lossSeverity(lossPct);
  if (severity === null) return undefined;
  const color = lossColor(severity);
  const tint = Math.round(MIN_TINT_PCT + severity * (MAX_TINT_PCT - MIN_TINT_PCT));
  return {
    boxShadow: `inset 3px 0 0 ${color}`,
    backgroundColor: `color-mix(in oklab, ${color} ${tint}%, transparent)`,
  };
}

/** "13.78%" for a recorded loss, "--" when the date has no recorded session. */
export function formatLoss(lossPct: number | null | undefined): string {
  return typeof lossPct === "number" && Number.isFinite(lossPct) ? `${lossPct.toFixed(2)}%` : "--";
}
