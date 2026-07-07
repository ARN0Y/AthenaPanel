import { cn } from "@/lib/utils";

interface GaugeProps {
  value: number; // 0..100
  label: string;
  sub?: string;
  size?: number;
}

export function Gauge({ value, label, sub, size = 96 }: GaugeProps) {
  const pct = Math.max(0, Math.min(100, value));
  const stroke = 8;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c - (pct / 100) * c;
  const color =
    pct >= 90 ? "stroke-destructive" : pct >= 70 ? "stroke-warning" : "stroke-primary";

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            strokeWidth={stroke}
            className="stroke-muted"
            fill="none"
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={offset}
            className={cn("fill-none transition-all duration-500", color)}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-bold tabular-nums">{Math.round(pct)}%</span>
        </div>
      </div>
      <div className="text-center">
        <div className="text-sm font-medium">{label}</div>
        {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
      </div>
    </div>
  );
}
