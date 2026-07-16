import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowDownToLine,
  ArrowUp,
  ArrowUpFromLine,
  ChevronsUpDown,
  PowerOff,
  RefreshCw,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/widgets/PageHeader";
import { OnlineDot, ProtocolBadge } from "@/components/widgets/StatusBadge";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { api, ApiError, type Session } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatBps, formatBytes, formatDuration } from "@/lib/format";

type SortKey = "user" | "protocol" | "uptime" | "down" | "up" | "total";

const numericKeys: SortKey[] = ["uptime", "down", "up", "total"];

function value(s: Session, key: SortKey): string | number {
  switch (key) {
    case "user":
      return (s.username || "").toLowerCase();
    case "protocol":
      return (s.protocol || "").toLowerCase();
    case "uptime":
      return s.uptime_seconds;
    case "down":
      return s.tx_rate_bps;
    case "up":
      return s.rx_rate_bps;
    case "total":
      return (s.rx_bytes || 0) + (s.tx_bytes || 0);
  }
}

export function Sessions() {
  const qc = useQueryClient();
  const { data: sessions = [], isLoading, isFetching, refetch } = useQuery({
    queryKey: ["sessions"],
    queryFn: api.listSessions,
    refetchInterval: 3000,
  });
  const [target, setTarget] = React.useState<string | null>(null);
  const [sort, setSort] = React.useState<{ key: SortKey; dir: "asc" | "desc" }>({
    key: "down",
    dir: "desc",
  });

  const toggleSort = (key: SortKey) =>
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: numericKeys.includes(key) ? "desc" : "asc" },
    );

  const sorted = React.useMemo(() => {
    const arr = [...sessions];
    arr.sort((a, b) => {
      const av = value(a, sort.key);
      const bv = value(b, sort.key);
      if (av < bv) return sort.dir === "asc" ? -1 : 1;
      if (av > bv) return sort.dir === "asc" ? 1 : -1;
      return 0;
    });
    return arr;
  }, [sessions, sort]);

  const disconnectMut = useMutation({
    mutationFn: (username: string) => api.disconnect(username),
    onSuccess: (res) => {
      toast.success(res.detail);
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Disconnect failed"),
  });

  const totalDown = sessions.reduce((a, s) => a + s.tx_rate_bps, 0);
  const totalUp = sessions.reduce((a, s) => a + s.rx_rate_bps, 0);
  const protoCount = (p: string) =>
    sessions.filter((s) => (s.protocol || "").toUpperCase() === p).length;
  const breakdown = [
    { key: "L2TP", n: protoCount("L2TP"), dot: "bg-sky-400" },
    { key: "SSTP", n: protoCount("SSTP"), dot: "bg-violet-400" },
    { key: "WG", n: protoCount("WIREGUARD"), dot: "bg-emerald-400" },
  ].filter((b) => b.n > 0);

  const SortHead = ({
    label,
    k,
    align = "left",
    className,
  }: {
    label: string;
    k: SortKey;
    align?: "left" | "right";
    className?: string;
  }) => {
    const active = sort.key === k;
    return (
      <TableHead className={cn(align === "right" && "text-right", className)}>
        <button
          type="button"
          onClick={() => toggleSort(k)}
          className={cn(
            "inline-flex items-center gap-1 text-xs font-medium transition-colors hover:text-foreground",
            active ? "text-foreground" : "text-muted-foreground",
            align === "right" && "flex-row-reverse",
          )}
        >
          {label}
          {active ? (
            sort.dir === "asc" ? (
              <ArrowUp className="h-3 w-3 text-primary" />
            ) : (
              <ArrowDown className="h-3 w-3 text-primary" />
            )
          ) : (
            <ChevronsUpDown className="h-3 w-3 opacity-40" />
          )}
        </button>
      </TableHead>
    );
  };

  return (
    <div>
      <PageHeader
        title="Live sessions"
        description="Connected clients · refreshes every 3s · click a column to sort"
        actions={
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
        }
      />

      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-success/15 text-success">
              <OnlineDot online />
            </div>
            <div>
              <div className="text-2xl font-bold">{sessions.length}</div>
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
                <span>online</span>
                {breakdown.map((b) => (
                  <span key={b.key} className="inline-flex items-center gap-1">
                    <span className={`h-1.5 w-1.5 rounded-full ${b.dot}`} /> {b.n} {b.key}
                  </span>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[hsl(var(--chart-rx))]/15 text-[hsl(var(--chart-rx))]">
              <ArrowDownToLine className="h-5 w-5" />
            </div>
            <div>
              <div className="text-2xl font-bold tabular-nums">{formatBps(totalDown)}</div>
              <div className="text-xs text-muted-foreground">total download</div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[hsl(var(--chart-tx))]/15 text-[hsl(var(--chart-tx))]">
              <ArrowUpFromLine className="h-5 w-5" />
            </div>
            <div>
              <div className="text-2xl font-bold tabular-nums">{formatBps(totalUp)}</div>
              <div className="text-xs text-muted-foreground">total upload</div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <SortHead label="User" k="user" />
                  <SortHead label="Protocol" k="protocol" />
                  <TableHead>IP</TableHead>
                  <SortHead label="Connected" k="uptime" />
                  <SortHead label="↓ Down" k="down" align="right" />
                  <SortHead label="↑ Up" k="up" align="right" />
                  <SortHead label="Usage" k="total" align="right" />
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading && (
                  <TableRow>
                    <TableCell colSpan={8} className="py-10 text-center text-muted-foreground">
                      Loading…
                    </TableCell>
                  </TableRow>
                )}
                {!isLoading && sorted.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={8} className="py-10 text-center text-muted-foreground">
                      No active sessions
                    </TableCell>
                  </TableRow>
                )}
                {sorted.map((s) => (
                  <TableRow key={s.ifname} className="group">
                    <TableCell>
                      <div className="flex items-center gap-2 font-medium">
                        <OnlineDot online />
                        <span className="truncate">{s.username}</span>
                      </div>
                      <div className="pl-4 font-mono text-[10px] text-muted-foreground/70">{s.ifname}</div>
                    </TableCell>
                    <TableCell>
                      <ProtocolBadge protocol={s.protocol} />
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{s.ip}</TableCell>
                    <TableCell className="text-sm tabular-nums">{formatDuration(s.uptime_seconds)}</TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums text-[hsl(var(--chart-rx))]">
                      {formatBps(s.tx_rate_bps)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums text-[hsl(var(--chart-tx))]">
                      {formatBps(s.rx_rate_bps)}
                    </TableCell>
                    <TableCell className="text-right text-xs font-medium tabular-nums">
                      {formatBytes((s.rx_bytes || 0) + (s.tx_bytes || 0))}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 opacity-60 transition-opacity group-hover:opacity-100"
                        title="Disconnect"
                        onClick={() => setTarget(s.username)}
                      >
                        <PowerOff className="h-4 w-4 text-destructive" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!target}
        onOpenChange={(o) => !o && setTarget(null)}
        title={`Disconnect ${target}?`}
        description="The client is dropped immediately and may reconnect."
        confirmLabel="Disconnect"
        onConfirm={() => {
          if (target) disconnectMut.mutate(target);
          setTarget(null);
        }}
      />
    </div>
  );
}
