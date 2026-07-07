import { Lock, ShieldCheck, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { User } from "@/lib/api";
import { cn } from "@/lib/utils";

export const PROTOCOLS = {
  WIREGUARD: { label: "WireGuard", icon: Zap, cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300", title: "WireGuard (UDP/51820)" },
  SSTP: { label: "SSTP", icon: Lock, cls: "border-violet-500/30 bg-violet-500/10 text-violet-300", title: "SSTP over TLS (TCP/443)" },
  L2TP: { label: "L2TP", icon: ShieldCheck, cls: "border-sky-500/30 bg-sky-500/10 text-sky-300", title: "L2TP/IPsec (UDP)" },
} as const;

export function protoMeta(protocol: string) {
  const p = (protocol || "").toUpperCase();
  return p === "SSTP" ? PROTOCOLS.SSTP : p === "WIREGUARD" ? PROTOCOLS.WIREGUARD : PROTOCOLS.L2TP;
}

/** Protocol pill: WireGuard / SSTP / L2TP — color-coded + icon. */
export function ProtocolBadge({ protocol, className }: { protocol: string; className?: string }) {
  const m = protoMeta(protocol);
  const Icon = m.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-semibold tracking-wide",
        m.cls,
        className,
      )}
      title={m.title}
    >
      <Icon className="h-3 w-3" />
      {m.label}
    </span>
  );
}

export function OnlineDot({ online }: { online: boolean }) {
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {online && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60" />
      )}
      <span
        className={cn(
          "relative inline-flex h-2.5 w-2.5 rounded-full",
          online ? "bg-success" : "bg-muted-foreground/40",
        )}
      />
    </span>
  );
}

export function UserStatusBadge({ user }: { user: User }) {
  if (!user.is_active) return <Badge variant="secondary">Disabled</Badge>;
  if (user.is_expired) return <Badge variant="destructive">Expired</Badge>;
  if (user.quota_exceeded) return <Badge variant="warning">Quota full</Badge>;
  if (user.online)
    return (
      <Badge variant="success" className="gap-1.5">
        <OnlineDot online /> Online
      </Badge>
    );
  return <Badge variant="outline">Offline</Badge>;
}
