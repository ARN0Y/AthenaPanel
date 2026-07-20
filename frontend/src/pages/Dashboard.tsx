import * as React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  BatteryLow,
  Cpu,
  Gauge as GaugeIcon,
  Radio,
  TrendingUp,
  Users as UsersIcon,
  Wifi,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatCard } from "@/components/widgets/StatCard";
import { OnlineDot, ProtocolBadge } from "@/components/widgets/StatusBadge";
import { QuotaBar } from "@/components/widgets/QuotaBar";
import { TrafficChart } from "@/components/charts/TrafficChart";
import { useAuth } from "@/hooks/useAuth";
import { Gauge } from "@/components/charts/Gauge";
import { api } from "@/lib/api";
import { formatBps, formatBytes, formatDuration, formatUptime } from "@/lib/format";

function SystemPanel() {
  const { data, isLoading } = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 4000 });
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Cpu className="h-4 w-4 text-muted-foreground" /> System
        </CardTitle>
        {data && (
          <span className="font-mono text-xs text-muted-foreground">
            {data.hostname} · up {formatUptime(data.uptime_seconds)}
          </span>
        )}
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <div className="grid grid-cols-3 gap-4">
            {[0, 1, 2].map((i) => (
              <div key={i} className="mx-auto h-24 w-24 animate-pulse rounded-full bg-muted" />
            ))}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-4">
              <Gauge value={data.cpu_percent} label="CPU" sub={`load ${data.load_1}`} />
              <Gauge value={data.mem_percent} label="Memory" sub={`${formatBytes(data.mem_used)} / ${formatBytes(data.mem_total)}`} />
              <Gauge value={data.disk_percent} label="Disk" sub={`${formatBytes(data.disk_used)} / ${formatBytes(data.disk_total)}`} />
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 border-t pt-4 text-sm">
              <div className="flex items-center gap-2">
                <ArrowDownToLine className="h-4 w-4 text-[hsl(var(--chart-rx))]" />
                <span className="text-muted-foreground">NIC in</span>
                <span className="ml-auto font-mono">{formatBps(data.net_rx_bps)}</span>
              </div>
              <div className="flex items-center gap-2">
                <ArrowUpFromLine className="h-4 w-4 text-[hsl(var(--chart-tx))]" />
                <span className="text-muted-foreground">NIC out</span>
                <span className="ml-auto font-mono">{formatBps(data.net_tx_bps)}</span>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function Dashboard() {
  const [range, setRange] = React.useState("60");
  // Node-wide throughput and host health describe the whole server, so they are
  // superadmin-only on the API too. Skip the queries entirely for a reseller —
  // their stat cards below are already scoped to their own users.
  const { isSuperadmin } = useAuth();
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 5000 });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.listSessions, refetchInterval: 4000 });
  const history = useQuery({
    queryKey: ["traffic-history", range],
    queryFn: () => api.trafficHistory(Number(range)),
    refetchInterval: 15000,
    enabled: isSuperadmin,
  });
  const s = stats.data;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Online now"
          value={s?.online_count ?? "—"}
          icon={Wifi}
          accent="success"
          hint={`${s?.total_users ?? 0} total users`}
          loading={stats.isLoading}
        />
        <StatCard
          title="Live throughput"
          value={s ? formatBps(s.rx_rate_bps + s.tx_rate_bps) : "—"}
          icon={TrendingUp}
          hint={s ? `↓ ${formatBps(s.tx_rate_bps)} · ↑ ${formatBps(s.rx_rate_bps)}` : ""}
          loading={stats.isLoading}
        />
        <StatCard
          title="Traffic today"
          value={s ? formatBytes(s.traffic_today_bytes) : "—"}
          icon={GaugeIcon}
          hint={s ? `${formatBytes(s.traffic_total_bytes)} all-time` : ""}
          loading={stats.isLoading}
        />
        <StatCard
          title="Alerts"
          value={s ? s.quota_warnings + s.expired_users : "—"}
          icon={UsersIcon}
          accent={s && s.quota_warnings + s.expired_users > 0 ? "warning" : "primary"}
          hint={s ? `${s.quota_warnings} near quota · ${s.expired_users} expired` : ""}
          loading={stats.isLoading}
        />
      </div>

      {isSuperadmin && (
      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-base">Network throughput</CardTitle>
            <Select value={range} onValueChange={setRange}>
              <SelectTrigger className="h-8 w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="30">Last 30m</SelectItem>
                <SelectItem value="60">Last 1h</SelectItem>
                <SelectItem value="360">Last 6h</SelectItem>
                <SelectItem value="1440">Last 24h</SelectItem>
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent>
            {history.data && history.data.length > 1 ? (
              <TrafficChart data={history.data} />
            ) : (
              <div className="flex h-[260px] items-center justify-center text-sm text-muted-foreground">
                Collecting data… throughput appears as sessions generate traffic.
              </div>
            )}
            <div className="mt-2 flex items-center justify-center gap-6 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-[hsl(var(--chart-rx))]" /> Download
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-[hsl(var(--chart-tx))]" /> Upload
              </span>
            </div>
          </CardContent>
        </Card>

        <SystemPanel />
      </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Radio className="h-4 w-4 text-muted-foreground" /> Active sessions
            </CardTitle>
            <Link to="/sessions" className="text-xs text-primary hover:underline">
              View all
            </Link>
          </CardHeader>
          <CardContent>
            {sessions.data && sessions.data.length > 0 ? (
              <div className="space-y-1">
                {sessions.data.slice(0, 6).map((sess) => (
                  <div key={sess.ifname} className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-muted/50">
                    <OnlineDot online />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">{sess.username}</span>
                        <ProtocolBadge protocol={sess.protocol} />
                      </div>
                      <div className="font-mono text-xs text-muted-foreground">{sess.ip}</div>
                    </div>
                    <div className="text-right text-xs">
                      <div className="text-[hsl(var(--chart-rx))]">↓ {formatBps(sess.tx_rate_bps)}</div>
                      <div className="text-[hsl(var(--chart-tx))]">↑ {formatBps(sess.rx_rate_bps)}</div>
                    </div>
                    <div className="w-16 text-right text-xs text-muted-foreground">
                      {formatDuration(sess.uptime_seconds)}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">No active sessions</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Top users by usage</CardTitle>
          </CardHeader>
          <CardContent>
            {s?.top_users && s.top_users.length > 0 ? (
              <div className="space-y-3">
                {s.top_users.map((u) => (
                  <div key={u.username} className="flex items-center gap-3">
                    <div className="flex w-28 items-center gap-2">
                      <OnlineDot online={u.online} />
                      <span className="truncate text-sm font-medium">{u.username}</span>
                    </div>
                    <div className="flex-1">
                      <QuotaBar used={u.used_bytes} quota={u.quota_bytes} />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">No usage yet</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <BatteryLow className="h-4 w-4 text-muted-foreground" /> Users running low on data
          </CardTitle>
          <Link to="/users" className="text-xs text-primary hover:underline">
            View all
          </Link>
        </CardHeader>
        <CardContent>
          {s?.near_quota && s.near_quota.length > 0 ? (
            <div className="divide-y">
              {s.near_quota.map((u) => {
                const remaining = Math.max(0, u.quota_bytes - u.used_bytes);
                const over = u.used_bytes >= u.quota_bytes;
                return (
                  <div key={u.username} className="flex items-center gap-3 py-2.5 text-sm">
                    <OnlineDot online={u.online} />
                    <span className="w-28 shrink-0 truncate font-medium sm:w-40">{u.username}</span>
                    <div className="min-w-0 flex-1">
                      <QuotaBar used={u.used_bytes} quota={u.quota_bytes} />
                    </div>
                    <span className="w-12 shrink-0 text-right font-mono text-xs tabular-nums text-muted-foreground">
                      {Math.round(u.percent)}%
                    </span>
                    <span
                      className={`hidden w-24 shrink-0 text-right font-mono text-xs tabular-nums sm:inline ${
                        over ? "text-destructive" : "text-muted-foreground"
                      }`}
                    >
                      {over ? "over limit" : `${formatBytes(remaining)} left`}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">No users have a data limit set</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
