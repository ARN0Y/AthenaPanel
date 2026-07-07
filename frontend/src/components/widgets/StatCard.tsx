import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface StatCardProps {
  title: string;
  value: React.ReactNode;
  icon: React.ComponentType<{ className?: string }>;
  hint?: React.ReactNode;
  accent?: "primary" | "success" | "warning" | "destructive";
  loading?: boolean;
}

const accentMap = {
  primary: "bg-primary/15 text-primary",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  destructive: "bg-destructive/15 text-destructive",
};

export function StatCard({ title, value, icon: Icon, hint, accent = "primary", loading }: StatCardProps) {
  return (
    <Card className="relative overflow-hidden transition-shadow hover:shadow-md">
      <CardContent className="flex items-center gap-4 p-5">
        <div className={cn("flex h-12 w-12 shrink-0 items-center justify-center rounded-xl", accentMap[accent])}>
          <Icon className="h-6 w-6" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</p>
          <p className="mt-0.5 text-2xl font-bold tabular-nums">
            {loading ? <span className="inline-block h-7 w-16 animate-pulse rounded bg-muted" /> : value}
          </p>
          {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
        </div>
      </CardContent>
    </Card>
  );
}
