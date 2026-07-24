import * as React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  BatteryLow,
  ChevronRight,
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
import { ProtocolMix } from "@/components/widgets/ProtocolMix";
import { QuotaBar } from "@/components/widgets/QuotaBar";
import { TrafficChart } from "@/components/charts/TrafficChart";
import { useAuth } from "@/hooks/useAuth";
import { Gauge } from "@/components/charts/Gauge";
import { api } from "@/lib/api";
import { formatBps, formatBytes, formatDuration, formatUptime } from "@/lib/format";

/** Trend from the first vs. second half of a short series → { delta%, good }. */
function trendOf(series: number[], goodWhenUp = true): { delta: number; good: boolean } | null {
  if (series.length < 4) return null;
  const mid = Math.floor(series.length / 2);
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  const prev = avg(series.slice(0, mid));
  const cur = avg(series.slice(mid));
  if (prev <= 0) return null;
  const delta = ((cur - prev) / prev) * 100;
  if (Math.abs(delta) < 1) return null;
  return { delta, good: goodWhenUp ? delta >= 0 : delta < 0 };
}

function CardChrome({
  icon: Icon,
  title,
  action,
  index = 0,
  className,
  children,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: React.ReactNode;
  action?: React.ReactNode;
  index?: number;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <Card className={`rise ${className ?? ""}`} style={{ ["--i" as string]: index }}>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="flex items-center gap-2 text-[15px]">
          {Icon && <Icon className="h-4 w-4 text-muted-foreground" />}
          {title}
        </CardTitle>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function ViewAll({ to }: { to: string }) {
  return (
    <Link to={to} className="inline-flex items-center gap-0.5 text-xs font-medium text-primary hover:underline">
      View all <ChevronRight className="h-3.5 w-3.5" />
    </Link>
  );
}

function SystemPanel({ index }: { index: number }) {
  const { data, isLoading } = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 4000 });
  return (
    <CardChrome
      icon={Cpu}
      title="System"
      index={index}
      action={
        data && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {data.hostname} · up {formatUptime(data.uptime_seconds)}
          </span>
        )
      }
    >
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
          <div className="mt-4 grid grid-cols-2 gap-2 border-t pt-4">
            <div className="flex items-center gap-2 rounded-lg bg-muted/40 px-3 py-2 text-sm">
              <ArrowDownToLine className="h-4 w-4 text-[hsl(var(--chart-rx))]" />
              <span className="text-muted-foreground">NIC in</span>
              <span className="ml-auto font-mono tabnum">{formatBps(data.net_rx_bps)}</span>
            </div>
            <div className="flex items-center gap-2 rounded-lg bg-muted/40 px-3 py-2 text-sm">
              <ArrowUpFromLine className="h-4 w-4 text-[hsl(var(--chart-tx))]" />
              <span className="text-muted-foreground">NIC out</span>
              <span className="ml-auto font-mono tabnum">{formatBps(data.net_tx_bps)}</span>
            </div>
          </div>
        </>
      )}
    </CardChrome>
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
  const sess = sessions.data ?? [];

  // Sparklines / trends from the throughput history (superadmin only).
  const hist = history.data ?? [];
  const thrSpark = hist.map((p) => p.rx_bps + p.tx_bps);
  const onlineSpark = hist.map((p) => p.online_count);
  const thrTrend = trendOf(thrSpark, true);
  const onlineTrend = trendOf(onlineSpark, true);
  const alertCount = s ? s.quota_warnings + s.expired_users : 0;

  return (
    <div className="space-y-6">
      <div className="rise" style={{ ["--i" as string]: 0 }}>
        <h1 className="text-xl font-bold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">Live status of your network at a glance.</p>
      </div>

      {/* Hero KPIs */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          index={1}
          title="Online now"
          value={s?.online_count ?? "—"}
          icon={Wifi}
          accent="success"
          live={!!s?.online_count}
          hint={`${s?.total_users ?? 0} total users`}
          trend={onlineTrend}
          spark={onlineSpark}
          loading={stats.isLoading}
        />
        <StatCard
          index={2}
          title="Live throughput"
          value={s ? formatBps(s.rx_rate_bps + s.tx_rate_bps) : "—"}
          icon={TrendingUp}
          hint={s ? `↓ ${formatBps(s.tx_rate_bps)} · ↑ ${formatBps(s.rx_rate_bps)}` : ""}
          trend={thrTrend}
          spark={thrSpark}
          loading={stats.isLoading}
        />
        <StatCard
          index={3}
          title="Traffic today"
          value={s ? formatBytes(s.traffic_today_bytes) : "—"}
          icon={GaugeIcon}
          hint={s ? `${formatBytes(s.traffic_total_bytes)} all-time` : ""}
          loading={stats.isLoading}
        />
        <StatCard
          index={4}
          title="Alerts"
          value={s ? alertCount : "—"}
          icon={UsersIcon}
          accent={alertCount > 0 ? "warning" : "primary"}
          hint={s ? `${s.quota_warnings} near quota · ${s.expired_users} expired` : ""}
          loading={stats.isLoading}
        />
      </div>

      {isSuperadmin && (
        <div className="grid gap-6 lg:grid-cols-3">
          <CardChrome
            index={5}
            className="lg:col-span-2"
            title={
              <span className="flex items-center gap-2">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
                </span>
                Network throughput
              </span>
            }
            action={
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
            }
          >
            {hist.length > 1 ? (
              <TrafficChart data={hist} />
            ) : (
              <div className="flex h-[264px] items-center justify-center text-sm text-muted-foreground">
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
          </CardChrome>

          <SystemPanel index={6} />
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <CardChrome index={7} icon={Radio} title="Active sessions" action={<ViewAll to="/sessions" />}>
          {sess.length > 0 && <ProtocolMix sessions={sess} className="mb-4" />}
          {sess.length > 0 ? (
            <div className="-mx-2 space-y-0.5">
              {sess.slice(0, 6).map((session) => (
                <div key={session.ifname} className="flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-muted/60">
                  <OnlineDot online />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{session.username}</span>
                      <ProtocolBadge protocol={session.protocol} />
                    </div>
                    <div className="font-mono text-xs text-muted-foreground">{session.ip}</div>
                  </div>
                  <div className="text-right text-xs tabnum">
                    <div className="text-[hsl(var(--chart-rx))]">↓ {formatBps(session.tx_rate_bps)}</div>
                    <div className="text-[hsl(var(--chart-tx))]">↑ {formatBps(session.rx_rate_bps)}</div>
                  </div>
                  <div className="w-16 text-right text-xs text-muted-foreground tabnum">
                    {formatDuration(session.uptime_seconds)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-10 text-center text-sm text-muted-foreground">No active sessions</p>
          )}
        </CardChrome>

        <CardChrome index={8} icon={TrendingUp} title="Top users by usage">
          {s?.top_users && s.top_users.length > 0 ? (
            <div className="space-y-3.5">
              {s.top_users.map((u, i) => (
                <div key={u.username} className="flex items-center gap-3">
                  <span className="w-4 text-center text-xs font-semibold text-muted-foreground tabnum">{i + 1}</span>
                  <div className="flex w-24 items-center gap-2 sm:w-28">
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
            <p className="py-10 text-center text-sm text-muted-foreground">No usage yet</p>
          )}
        </CardChrome>
      </div>

      <CardChrome index={9} icon={BatteryLow} title="Users running low on data" action={<ViewAll to="/users" />}>
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
          <p className="py-10 text-center text-sm text-muted-foreground">No users have a data limit set</p>
        )}
      </CardChrome>
    </div>
  );
}
