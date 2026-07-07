import { Progress } from "@/components/ui/progress";
import { formatBytes, formatQuota } from "@/lib/format";
import { cn } from "@/lib/utils";

export function QuotaBar({ used, quota }: { used: number; quota: number }) {
  if (!quota || quota <= 0) {
    return (
      <div className="space-y-1">
        <div className="text-xs text-muted-foreground">
          {formatBytes(used)} <span className="opacity-50">/ Unlimited</span>
        </div>
      </div>
    );
  }
  const pct = Math.min(100, Math.round((used / quota) * 100));
  const indicator =
    pct >= 95 ? "bg-destructive" : pct >= 80 ? "bg-warning" : "bg-primary";
  return (
    <div className="min-w-[140px] space-y-1.5">
      <Progress value={pct} indicatorClassName={indicator} />
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{formatBytes(used)}</span>
        <span className={cn(pct >= 80 && "font-medium text-foreground")}>{pct}%</span>
        <span>{formatQuota(quota)}</span>
      </div>
    </div>
  );
}
