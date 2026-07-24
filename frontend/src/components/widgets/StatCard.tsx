import { ArrowDownRight, ArrowUpRight } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Sparkline } from "@/components/charts/Sparkline";
import { cn } from "@/lib/utils";

type Accent = "primary" | "success" | "warning" | "destructive";

interface StatCardProps {
  title: string;
  value: React.ReactNode;
  icon: React.ComponentType<{ className?: string }>;
  hint?: React.ReactNode;
  accent?: Accent;
  loading?: boolean;
  /** Optional trend badge, e.g. { delta: 12, good: true } → green ▲ 12%. */
  trend?: { delta: number; good?: boolean } | null;
  /** Optional mini series drawn as a sparkline under the value. */
  spark?: number[];
  /** Pulsing "live" ring around the icon (e.g. the online-now tile). */
  live?: boolean;
  /** Stagger index for the entrance animation. */
  index?: number;
}

const accentMap: Record<Accent, { tile: string; token: string }> = {
  primary: { tile: "from-primary/20 to-primary/5 text-primary", token: "--primary" },
  success: { tile: "from-success/20 to-success/5 text-success", token: "--success" },
  warning: { tile: "from-warning/20 to-warning/5 text-warning", token: "--warning" },
  destructive: { tile: "from-destructive/20 to-destructive/5 text-destructive", token: "--destructive" },
};

export function StatCard({
  title,
  value,
  icon: Icon,
  hint,
  accent = "primary",
  loading,
  trend,
  spark,
  live,
  index = 0,
}: StatCardProps) {
  const a = accentMap[accent];
  return (
    <Card
      className="rise accent-top group relative overflow-hidden transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/5"
      style={{ ["--i" as string]: index, ["--tint" as string]: `var(${a.token})` }}
    >
      <CardContent className="p-5">
        <div className="flex items-start gap-4">
          <div
            className={cn(
              "relative flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ring-1 ring-inset ring-border/50",
              a.tile,
              live && "live-ring",
            )}
          >
            <Icon className="h-[22px] w-[22px]" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <p className="truncate text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</p>
              {trend && !loading && (
                <span
                  className={cn(
                    "inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11px] font-semibold tabnum",
                    trend.good ? "bg-success/12 text-success" : "bg-destructive/12 text-destructive",
                  )}
                >
                  {trend.good ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
                  {Math.abs(Math.round(trend.delta))}%
                </span>
              )}
            </div>
            <p className="mt-1 text-[26px] font-bold leading-none tabnum">
              {loading ? <span className="inline-block h-7 w-20 animate-pulse rounded bg-muted" /> : value}
            </p>
            {hint && <p className="mt-1.5 truncate text-xs text-muted-foreground">{hint}</p>}
          </div>
        </div>
        {spark && spark.length > 1 && !loading && (
          <div className="mt-3 -mb-1">
            <Sparkline data={spark} stroke={`var(${a.token})`} width={260} height={34} className="w-full" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
