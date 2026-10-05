/**
 * Trend indication, not analysis (Design Brief §7): no axes, no gridlines, one
 * endpoint dot. Hidden from screen readers; the card states the figures.
 */
export function Sparkline({ points, width = 240, height = 40 }: { points: number[]; width?: number; height?: number }) {
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = width / (points.length - 1);
  const coords = points.map((p, i) => [i * step, height - 3 - ((p - min) / span) * (height - 6)] as const);
  const d = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lx, ly] = coords[coords.length - 1];
  return (
    <svg
      className="kg-spark"
      viewBox={`0 0 ${width} ${height}`}
      style={{ width: "100%", height: "auto", display: "block", marginTop: 8 }}
      aria-hidden="true"
      focusable="false"
    >
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth={1.75} />
      <circle cx={lx} cy={ly} r={2.5} fill="var(--accent)" />
    </svg>
  );
}
