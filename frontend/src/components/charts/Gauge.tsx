import * as React from "react";

import { cn } from "@/lib/utils";

interface GaugeProps {
  value: number; // 0..100
  label: string;
  sub?: string;
  size?: number;
}

export function Gauge({ value, label, sub, size = 96 }: GaugeProps) {
  const id = React.useId();
  const pct = Math.max(0, Math.min(100, value));
  const stroke = 9;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c - (pct / 100) * c;
  // Colour tiers: calm indigo → amber → red as load climbs.
  const token = pct >= 90 ? "--destructive" : pct >= 70 ? "--warning" : "--primary";

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90" style={{ color: `hsl(var(${token}))` }}>
          <defs>
            <linearGradient id={`g-${id}`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="currentColor" stopOpacity={0.75} />
              <stop offset="100%" stopColor="currentColor" stopOpacity={1} />
            </linearGradient>
          </defs>
          <circle cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} className="stroke-muted/70" fill="none" />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={offset}
            stroke={`url(#g-${id})`}
            className="fill-none transition-[stroke-dashoffset] duration-700 ease-out"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-[19px] font-bold leading-none tabnum" style={{ color: `hsl(var(${token}))` }}>
            {Math.round(pct)}
            <span className="ml-0.5 text-[11px] font-semibold opacity-70">%</span>
          </span>
        </div>
      </div>
      <div className="text-center">
        <div className={cn("text-sm font-semibold")}>{label}</div>
        {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
      </div>
    </div>
  );
}
