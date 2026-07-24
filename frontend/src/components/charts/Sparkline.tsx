import * as React from "react";

/**
 * Tiny dependency-free sparkline (inline SVG). Draws a filled area + line for a
 * short series — used inside KPI tiles where a full recharts chart would be
 * overkill. Colour comes from the CSS var passed as `stroke` (a token like
 * "var(--chart-rx)"), so it stays theme-aware in light and dark.
 */
export function Sparkline({
  data,
  stroke = "var(--primary)",
  width = 96,
  height = 32,
  strokeWidth = 1.75,
  className,
}: {
  data: number[];
  stroke?: string;
  width?: number;
  height?: number;
  strokeWidth?: number;
  className?: string;
}) {
  const id = React.useId();
  const pts = data.filter((n) => Number.isFinite(n));
  if (pts.length < 2) {
    // Not enough points to draw a trend — keep the tile height stable.
    return <div style={{ width, height }} className={className} aria-hidden />;
  }

  const max = Math.max(...pts);
  const min = Math.min(...pts);
  const span = max - min || 1;
  const pad = strokeWidth; // keep the stroke inside the viewbox
  const stepX = (width - pad * 2) / (pts.length - 1);
  const y = (v: number) => pad + (height - pad * 2) * (1 - (v - min) / span);

  const coords = pts.map((v, i) => [pad + i * stepX, y(v)] as const);
  const line = coords.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(2)} ${py.toFixed(2)}`).join(" ");
  const area = `${line} L${coords[coords.length - 1][0].toFixed(2)} ${height} L${coords[0][0].toFixed(2)} ${height} Z`;
  const [lastX, lastY] = coords[coords.length - 1];

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={className}
      style={{ color: `hsl(${stroke})` }}
      aria-hidden
    >
      <defs>
        <linearGradient id={`sparkfill-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity={0.28} />
          <stop offset="100%" stopColor="currentColor" stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#sparkfill-${id})`} stroke="none" />
      <path d={line} fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lastX} cy={lastY} r={strokeWidth + 0.5} fill="currentColor" />
    </svg>
  );
}
