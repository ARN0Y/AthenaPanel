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

export function TrafficChart({ data }: { data: TrafficPoint[] }) {
  const series = data.map((p) => ({
    t: timeLabel(p.ts),
    down: Math.round(p.tx_bps),
    up: Math.round(p.rx_bps),
  }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={series} margin={{ top: 10, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="gDown" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(var(--chart-rx))" stopOpacity={0.4} />
            <stop offset="100%" stopColor="hsl(var(--chart-rx))" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="gUp" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(var(--chart-tx))" stopOpacity={0.35} />
            <stop offset="100%" stopColor="hsl(var(--chart-tx))" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
        <XAxis dataKey="t" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} tickLine={false} axisLine={false} minTickGap={32} />
        <YAxis
          tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
          width={64}
          tickFormatter={(v) => formatBps(v, 0)}
        />
        <Tooltip
          contentStyle={{
            background: "hsl(var(--popover))",
            border: "1px solid hsl(var(--border))",
            borderRadius: 8,
            fontSize: 12,
          }}
          formatter={(v: number, name) => [formatBps(v), name === "down" ? "Download" : "Upload"]}
        />
        <Area type="monotone" dataKey="down" stroke="hsl(var(--chart-rx))" strokeWidth={2} fill="url(#gDown)" />
        <Area type="monotone" dataKey="up" stroke="hsl(var(--chart-tx))" strokeWidth={2} fill="url(#gUp)" />
      </AreaChart>
    </ResponsiveContainer>
  );
}
