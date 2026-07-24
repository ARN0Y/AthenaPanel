import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { TrafficPoint } from "@/lib/api";
import { formatBps } from "@/lib/format";

function timeLabel(ts: string) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function TooltipCard({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border bg-popover/95 px-3 py-2 text-xs shadow-xl backdrop-blur">
      <div className="mb-1 font-medium text-muted-foreground">{label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="flex items-center gap-2 py-0.5">
          <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
          <span className="text-muted-foreground">{p.dataKey === "down" ? "Download" : "Upload"}</span>
          <span className="ml-auto font-mono font-medium tabnum text-foreground">{formatBps(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

export function TrafficChart({ data, height = 264 }: { data: TrafficPoint[]; height?: number }) {
  const series = data.map((p) => ({
    t: timeLabel(p.ts),
    down: Math.round(p.tx_bps),
    up: Math.round(p.rx_bps),
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={series} margin={{ top: 12, right: 10, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="gDown" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(var(--chart-rx))" stopOpacity={0.45} />
            <stop offset="70%" stopColor="hsl(var(--chart-rx))" stopOpacity={0.08} />
            <stop offset="100%" stopColor="hsl(var(--chart-rx))" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="gUp" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(var(--chart-tx))" stopOpacity={0.38} />
            <stop offset="70%" stopColor="hsl(var(--chart-tx))" stopOpacity={0.06} />
            <stop offset="100%" stopColor="hsl(var(--chart-tx))" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="2 5" stroke="hsl(var(--border))" strokeOpacity={0.6} vertical={false} />
        <XAxis
          dataKey="t"
          tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
          minTickGap={34}
          dy={4}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
          width={62}
          tickFormatter={(v) => formatBps(v, 0)}
        />
        <Tooltip content={<TooltipCard />} cursor={{ stroke: "hsl(var(--muted-foreground))", strokeOpacity: 0.3, strokeDasharray: "3 3" }} />
        <Area
          type="monotone"
          dataKey="down"
          stroke="hsl(var(--chart-rx))"
          strokeWidth={2.25}
          fill="url(#gDown)"
          // A pulsing dot on the latest sample reads as "live".
          dot={false}
          activeDot={{ r: 4 }}
          isAnimationActive={false}
        />
        <Area
          type="monotone"
          dataKey="up"
          stroke="hsl(var(--chart-tx))"
          strokeWidth={2.25}
          fill="url(#gUp)"
          dot={false}
          activeDot={{ r: 4 }}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
