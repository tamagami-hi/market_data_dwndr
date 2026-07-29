"use client";

/**
 * In-card visuals for the KPI strip.
 *
 * Both sit in a Metric's `accessory` slot — on the same row as the value — so they add
 * no height and the cards keep their existing size. Fixed width plus `overflow: hidden`
 * keeps the drawing inside the card at every breakpoint.
 *
 * Two shapes, chosen per metric rather than applied uniformly:
 *
 *   Sparkline  for quantities that vary over the session and where the SHAPE carries the
 *              meaning (frames/sec, drop rate, frame loss). A flat line is the healthy
 *              signal; a dip or spike is what you want to notice.
 *   MiniGauge  for quantities that are a fraction of a known whole (disk used, captures
 *              against the session baseline). A trend line would be a straight ramp and
 *              say nothing, whereas "how full" is the actual question.
 *
 * Both are `aria-hidden`: the number and its detail line already carry the information,
 * so a screen reader would only get noise.
 */

const TONE_STROKE: Record<string, string> = {
  neutral: "var(--accent)",
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--danger)",
};

export function Sparkline({
  values,
  tone = "neutral",
  width = 56,
  height = 18,
}: {
  values: number[];
  tone?: "neutral" | "success" | "warning" | "danger";
  width?: number;
  height?: number;
}) {
  // Two points minimum to draw a line; below that reserve the space so the card does
  // not reflow once samples start arriving.
  if (values.length < 2) {
    return <span style={{ width, height }} className="inline-block shrink-0" aria-hidden="true" />;
  }

  const stroke = TONE_STROKE[tone] ?? TONE_STROKE.neutral;
  const min = Math.min(...values);
  const max = Math.max(...values);
  // A constant series (a perfectly steady feed) has zero range; draw it mid-height
  // instead of dividing by zero.
  const range = max - min || 1;
  const pad = 1.5; // keep the stroke inside the viewBox rather than clipped at the edge
  const usable = height - pad * 2;

  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = pad + usable - ((value - min) / range) * usable;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="shrink-0 overflow-hidden"
      aria-hidden="true"
      focusable="false"
    >
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth="1.25"
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity="0.9"
      />
    </svg>
  );
}

export function MiniGauge({
  percent,
  tone = "neutral",
  width = 56,
  title,
}: {
  percent: number;
  tone?: "neutral" | "success" | "warning" | "danger";
  width?: number;
  title?: string;
}) {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(percent) ? percent : 0));
  const fill = TONE_STROKE[tone] ?? TONE_STROKE.neutral;
  // Bar only, no caption. A caption made the accessory 20px against the value's 19px, so
  // it became the tallest item in the row and grew every card by a pixel. The figure goes
  // in the tooltip instead; the card's detail line already carries the context.
  return (
    <span
      className="inline-flex shrink-0 items-center"
      style={{ width, height: 18 }}
      title={title ?? `${clamped.toFixed(clamped < 10 ? 1 : 0)}%`}
      aria-hidden="true"
    >
      <span
        className="w-full overflow-hidden rounded-full bg-[var(--surface-3)]"
        style={{ height: 4 }}
      >
        <span
          className="block h-full rounded-full"
          style={{ width: `${clamped}%`, background: fill }}
        />
      </span>
    </span>
  );
}
