import type { Session } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Protocol distribution of the currently-online sessions. Derived entirely from
 * the already-fetched session list (no extra API call). Each segment uses the
 * same colour family as its ProtocolBadge via protoMeta(), so the bar and the
 * per-row pills always agree.
 */

// Bar/legend colours keyed to the four protocol buckets. These mirror the
// border/text hues in StatusBadge.PROTOCOLS but as solid fills for the bar.
const SEGMENT: Record<string, { label: string; bar: string; dot: string }> = {
  L2TP: { label: "L2TP", bar: "bg-sky-500", dot: "bg-sky-500" },
  "L2TP-RAW": { label: "L2TP raw", bar: "bg-amber-500", dot: "bg-amber-500" },
  SSTP: { label: "SSTP", bar: "bg-violet-500", dot: "bg-violet-500" },
  WireGuard: { label: "WireGuard", bar: "bg-emerald-500", dot: "bg-emerald-500" },
};

function bucket(protocol: string): keyof typeof SEGMENT {
  const p = (protocol || "").toUpperCase();
  if (p === "L2TP-RAW" || p === "L2TP_RAW") return "L2TP-RAW";
  if (p === "SSTP") return "SSTP";
  if (p === "WIREGUARD") return "WireGuard";
  return "L2TP";
}

export function ProtocolMix({ sessions, className }: { sessions: Session[]; className?: string }) {
  const total = sessions.length;
  const counts = sessions.reduce<Record<string, number>>((acc, s) => {
    const k = bucket(s.protocol);
    acc[k] = (acc[k] || 0) + 1;
    return acc;
  }, {});
  // Stable, meaningful order; keep only protocols that are actually present.
  const order = ["L2TP", "L2TP-RAW", "SSTP", "WireGuard"] as const;
  const present = order.filter((k) => counts[k] > 0);

  if (total === 0) {
    return (
      <div className={cn("h-1.5 w-full rounded-full bg-muted", className)} aria-hidden />
    );
  }

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted">
        {present.map((k) => (
          <div
            key={k}
            className={cn("h-full transition-all duration-500", SEGMENT[k].bar)}
            style={{ width: `${(counts[k] / total) * 100}%` }}
            title={`${SEGMENT[k].label}: ${counts[k]}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {present.map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className={cn("h-2 w-2 rounded-full", SEGMENT[k].dot)} />
            {SEGMENT[k].label}
            <span className="font-semibold tabnum text-foreground">{counts[k]}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
