export type Severity = "neutral" | "success" | "warning" | "danger";

export function captureSeverity(state: {
  exhausted: boolean;
  stale: boolean;
  degraded: boolean;
}): Severity {
  if (state.exhausted || state.stale) return "danger";
  if (state.degraded) return "warning";
  return "success";
}

export function fpsSeverity(fps: number | null | undefined): Severity {
  if (fps == null) return "neutral";
  if (fps >= 0.9 && fps <= 1.1) return "success";
  if (fps === 0) return "danger";
  return "warning";
}

export function lossSeverity(lossPercent: number | null | undefined): Severity {
  if (!lossPercent || lossPercent < 0) return "neutral";
  if (lossPercent >= 1) return "danger";
  return "warning";
}
