import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownToLine, ArrowUpFromLine, PowerOff, RefreshCw } from "lucide-react";
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
import { api, ApiError } from "@/lib/api";
import { formatBps, formatBytes, formatDuration } from "@/lib/format";

export function Sessions() {
  const qc = useQueryClient();
  const { data: sessions = [], isLoading, isFetching, refetch } = useQuery({
    queryKey: ["sessions"],
    queryFn: api.listSessions,
    refetchInterval: 3000,
  });
  const [target, setTarget] = React.useState<string | null>(null);

  const disconnectMut = useMutation({
    mutationFn: (username: string) => api.disconnect(username),
    onSuccess: (res) => { toast.success(res.detail); qc.invalidateQueries({ queryKey: ["sessions"] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Disconnect failed"),
  });

  const totalDown = sessions.reduce((a, s) => a + s.tx_rate_bps, 0);
  const totalUp = sessions.reduce((a, s) => a + s.rx_rate_bps, 0);
  const protoCount = (p: string) => sessions.filter((s) => (s.protocol || "").toUpperCase() === p).length;
  const breakdown = [
    { key: "L2TP", n: protoCount("L2TP"), dot: "bg-sky-400" },
    { key: "SSTP", n: protoCount("SSTP"), dot: "bg-violet-400" },
    { key: "WG", n: protoCount("WIREGUARD"), dot: "bg-emerald-400" },
  ].filter((b) => b.n > 0);

  return (
    <div>
      <PageHeader
        title="Live sessions"
        description="Connected clients · refreshes every 3s"
        actions={
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
        }
      />

      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <Card><CardContent className="flex items-center gap-3 p-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-success/15 text-success"><OnlineDot online /></div>
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
        </CardContent></Card>
        <Card><CardContent className="flex items-center gap-3 p-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[hsl(var(--chart-rx))]/15 text-[hsl(var(--chart-rx))]"><ArrowDownToLine className="h-5 w-5" /></div>
          <div><div className="text-2xl font-bold tabular-nums">{formatBps(totalDown)}</div><div className="text-xs text-muted-foreground">total download</div></div>
        </CardContent></Card>
        <Card><CardContent className="flex items-center gap-3 p-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[hsl(var(--chart-tx))]/15 text-[hsl(var(--chart-tx))]"><ArrowUpFromLine className="h-5 w-5" /></div>
          <div><div className="text-2xl font-bold tabular-nums">{formatBps(totalUp)}</div><div className="text-xs text-muted-foreground">total upload</div></div>
        </CardContent></Card>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>User</TableHead>
                <TableHead>Protocol</TableHead>
                <TableHead>IP</TableHead>
                <TableHead>Interface</TableHead>
                <TableHead>Connected</TableHead>
                <TableHead className="text-right">↓ Rate</TableHead>
                <TableHead className="text-right">↑ Rate</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading && (
                <TableRow><TableCell colSpan={9} className="py-10 text-center text-muted-foreground">Loading…</TableCell></TableRow>
              )}
              {!isLoading && sessions.length === 0 && (
                <TableRow><TableCell colSpan={9} className="py-10 text-center text-muted-foreground">No active sessions</TableCell></TableRow>
              )}
              {sessions.map((s) => (
                <TableRow key={s.ifname}>
                  <TableCell>
                    <div className="flex items-center gap-2 font-medium">
                      <OnlineDot online /> {s.username}
                    </div>
                  </TableCell>
                  <TableCell><ProtocolBadge protocol={s.protocol} /></TableCell>
                  <TableCell className="font-mono text-xs">{s.ip}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{s.ifname}</TableCell>
                  <TableCell className="text-sm">{formatDuration(s.uptime_seconds)}</TableCell>
                  <TableCell className="text-right font-mono text-xs text-[hsl(var(--chart-rx))]">{formatBps(s.tx_rate_bps)}</TableCell>
                  <TableCell className="text-right font-mono text-xs text-[hsl(var(--chart-tx))]">{formatBps(s.rx_rate_bps)}</TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">{formatBytes(s.rx_bytes + s.tx_bytes)}</TableCell>
                  <TableCell>
                    <Button variant="ghost" size="icon" className="h-8 w-8" title="Disconnect" onClick={() => setTarget(s.username)}>
                      <PowerOff className="h-4 w-4 text-destructive" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!target}
        onOpenChange={(o) => !o && setTarget(null)}
        title={`Disconnect ${target}?`}
        description="The client is dropped immediately and may reconnect."
        confirmLabel="Disconnect"
        onConfirm={() => { if (target) disconnectMut.mutate(target); setTarget(null); }}
      />
    </div>
  );
}
