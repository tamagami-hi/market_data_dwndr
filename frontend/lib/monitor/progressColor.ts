const MIN_PROGRESS = 0;
const MAX_PROGRESS = 100;
const MAX_HUE = 120;

function clampProgress(value: number): number {
  if (!Number.isFinite(value)) return MIN_PROGRESS;
  return Math.max(MIN_PROGRESS, Math.min(MAX_PROGRESS, value));
}

export function progressHue(value: number): number {
  return (clampProgress(value) / MAX_PROGRESS) * MAX_HUE;
}

export function progressColor(value: number): string {
  return `hsl(${progressHue(value)} 72% 45%)`;
}
