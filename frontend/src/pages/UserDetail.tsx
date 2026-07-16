import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Ban,
  CalendarClock,
  CheckCircle2,
  Clock,
  Download,
  Gauge,
  Hash,
  Pencil,
  Power,
  RotateCcw,
  Shield,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/widgets/PageHeader";
import { ProfileCard } from "@/components/widgets/ProfileCard";
import { QuotaBar } from "@/components/widgets/QuotaBar";
import { UserStatusBadge } from "@/components/widgets/StatusBadge";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { UserFormDialog } from "@/components/UserFormDialog";
import { api, ApiError, type UserPayload, type WgConfig } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatBytes, formatDate, formatDuration, formatRate, relativeTime } from "@/lib/format";

function WireGuardCard({ userId }: { userId: number }) {
  const qc = useQueryClient();
  const { data: wg } = useQuery({
    queryKey: ["wg", userId],
    queryFn: () => api.wgStatus(userId),
    enabled: Number.isFinite(userId),
    refetchInterval: 8000,
  });
  const [cfg, setCfg] = React.useState<WgConfig | null>(null);
  const invalidate = () => { setCfg(null); qc.invalidateQueries({ queryKey: ["wg", userId] }); };

  const enableMut = useMutation({
    mutationFn: () => api.wgEnable(userId),
    onSuccess: () => { toast.success("WireGuard enabled"); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Enable failed"),
  });
  const disableMut = useMutation({
    mutationFn: () => api.wgDisable(userId),
    onSuccess: () => { toast.success("WireGuard disabled"); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Disable failed"),
  });
  const showConfigMut = useMutation({
    mutationFn: () => api.wgConfig(userId),
    onSuccess: (c) => setCfg(c),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not load config"),
  });

  const download = () => {
    if (!cfg) return;
    const blob = new Blob([cfg.config], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = cfg.filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <Card className="mt-6">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="flex items-center gap-2 text-base"><Shield className="h-4 w-4" /> WireGuard</CardTitle>
        {wg?.enabled && (
          <span className={cn("flex items-center gap-1.5 text-xs font-medium", wg.online ? "text-success" : "text-muted-foreground")}>
            <span className={cn("h-2 w-2 rounded-full", wg.online ? "bg-success" : "bg-muted-foreground/40")} />
            {wg.online ? "Online" : "Offline"}
          </span>
        )}
      </CardHeader>
      <CardContent>
        {!wg ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : !wg.enabled ? (
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-muted-foreground">Not enabled. Turn it on to give this account a WireGuard config (same quota as L2TP/SSTP).</p>
            <Button onClick={() => enableMut.mutate()} disabled={enableMut.isPending}>
              <Shield className="h-4 w-4" /> Enable
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="text-muted-foreground">Tunnel IP</span>
              <code className="rounded bg-muted px-2 py-0.5 font-mono">{wg.address}</code>
              <div className="ml-auto flex gap-2">
                <Button variant="outline" size="sm" onClick={() => showConfigMut.mutate()} disabled={showConfigMut.isPending}>
                  <Download className="h-4 w-4" /> Show config & QR
                </Button>
                <Button variant="outline" size="sm" className="text-destructive hover:text-destructive" onClick={() => disableMut.mutate()} disabled={disableMut.isPending}>
                  <Ban className="h-4 w-4" /> Disable
                </Button>
              </div>
            </div>
            {cfg && (
              <div className="grid gap-4 sm:grid-cols-[220px_1fr]">
                <div className="rounded-lg border bg-white p-2 [&>svg]:h-full [&>svg]:w-full" style={{ width: 220, height: 220 }}
                     dangerouslySetInnerHTML={{ __html: cfg.qr_svg }} />
                <div className="space-y-2">
                  <pre className="max-h-[200px] overflow-auto rounded-lg border bg-muted p-3 text-xs leading-relaxed">{cfg.config}</pre>
                  <Button size="sm" onClick={download}><Download className="h-4 w-4" /> Download {cfg.filename}</Button>
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function InfoTile({ icon: Icon, label, value }: { icon: React.ComponentType<{ className?: string }>; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border p-3">
      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-muted text-muted-foreground"><Icon className="h-4 w-4" /></div>
      <div className="min-w-0">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="truncate text-sm font-medium">{value}</div>
      </div>
    </div>
  );
}

export function UserDetail() {
  const { id } = useParams();
  const userId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [editOpen, setEditOpen] = React.useState(false);
  const [confirm, setConfirm] = React.useState<{ title: string; description: string; confirmLabel: string; action: () => void } | null>(null);

  const { data: user, isLoading } = useQuery({
    queryKey: ["user", userId],
    queryFn: () => api.getUser(userId),
    refetchInterval: 5000,
    enabled: Number.isFinite(userId),
  });
  const { data: events = [] } = useQuery({ queryKey: ["events", 500], queryFn: () => api.events(500) });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["user", userId] });
    qc.invalidateQueries({ queryKey: ["users"] });
  };

  const updateMut = useMutation({
    mutationFn: (p: UserPayload) => api.updateUser(userId, p),
    onSuccess: () => { toast.success("User updated"); setEditOpen(false); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Update failed"),
  });
  const toggleMut = useMutation({ mutationFn: () => api.toggleUser(userId), onSuccess: invalidate });
  const resetMut = useMutation({ mutationFn: () => api.resetQuota(userId), onSuccess: () => { toast.success("Quota reset"); invalidate(); } });
  const disconnectMut = useMutation({
    mutationFn: () => api.disconnect(user!.username),
    onSuccess: () => { toast.success("Disconnected"); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Disconnect failed"),
  });
  const deleteMut = useMutation({
    mutationFn: () => api.deleteUser(userId),
    onSuccess: () => { toast.success("User deleted"); navigate("/users"); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Delete failed"),
  });

  if (isLoading) return <div className="py-20 text-center text-muted-foreground">Loading…</div>;
  if (!user) return <div className="py-20 text-center text-muted-foreground">User not found.</div>;

  const userEvents = events.filter((e) => e.username === user.username).slice(0, 20);
  const pct = user.quota_bytes > 0 ? (user.used_bytes / user.quota_bytes) * 100 : 0;

  return (
    <div>
      <Button variant="ghost" size="sm" className="mb-3 -ml-2" onClick={() => navigate("/users")}>
        <ArrowLeft className="h-4 w-4" /> Back to users
      </Button>

      <PageHeader
        title={user.username}
        description={user.note || "VPN account"}
        actions={
          <>
            <UserStatusBadge user={user} />
            {user.online && (
              <Button variant="outline" size="sm" onClick={() => disconnectMut.mutate()}>
                <Power className="h-4 w-4" /> Disconnect
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={() => toggleMut.mutate()}>
              {user.is_active ? <><Ban className="h-4 w-4" /> Disable</> : <><CheckCircle2 className="h-4 w-4" /> Enable</>}
            </Button>
            <Button size="sm" onClick={() => setEditOpen(true)}>
              <Pencil className="h-4 w-4" /> Edit
            </Button>
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2"><CardTitle className="text-base">Quota usage</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <div className="text-3xl font-bold tabular-nums">{formatBytes(user.used_bytes)}</div>
                <div className="text-xs text-muted-foreground">
                  of {user.quota_bytes > 0 ? formatBytes(user.quota_bytes) : "unlimited"}
                  {user.quota_bytes > 0 && ` · ${formatBytes(Math.max(0, user.quota_bytes - user.used_bytes))} left`}
                </div>
              </div>
              {user.quota_bytes > 0 && (
                <div className="text-right leading-none">
                  <div
                    className={cn(
                      "text-2xl font-bold tabular-nums",
                      pct >= 100 ? "text-destructive" : pct >= 80 ? "text-warning" : "text-foreground",
                    )}
                  >
                    {Math.round(pct)}%
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">used</div>
                </div>
              )}
            </div>
            <QuotaBar used={user.used_bytes} quota={user.quota_bytes} />
            <Separator />
            <div className="grid gap-3 sm:grid-cols-2">
              <InfoTile icon={Gauge} label="Speed (down / up)" value={`${formatRate(user.rate_down_kbps)} / ${formatRate(user.rate_up_kbps)}`} />
              <InfoTile icon={Hash} label="Total sessions" value={user.total_sessions} />
              <InfoTile icon={Clock} label="Last seen" value={relativeTime(user.last_seen)} />
              <InfoTile icon={CalendarClock} label="Expires" value={user.expires_at ? formatDate(user.expires_at) : "Never"} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Quick actions</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <Button variant="outline" className="w-full justify-start" onClick={() => setConfirm({
              title: `Reset quota for ${user.username}?`, description: "Used traffic returns to 0.", confirmLabel: "Reset", action: () => resetMut.mutate(),
            })}>
              <RotateCcw className="h-4 w-4" /> Reset quota
            </Button>
            <Button variant="outline" className="w-full justify-start" onClick={() => toggleMut.mutate()}>
              {user.is_active ? <><Ban className="h-4 w-4" /> Disable account</> : <><CheckCircle2 className="h-4 w-4" /> Enable account</>}
            </Button>
            <Separator />
            <Button variant="outline" className="w-full justify-start text-destructive hover:text-destructive" onClick={() => setConfirm({
              title: `Delete ${user.username}?`, description: "This cannot be undone.", confirmLabel: "Delete", action: () => deleteMut.mutate(),
            })}>
              <Trash2 className="h-4 w-4" /> Delete user
            </Button>
            <div className="pt-2 text-xs text-muted-foreground">
              Created {formatDate(user.created_at)}
              {user.created_by_username && user.created_by_username !== "—" && <> · by {user.created_by_username}</>}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="mt-6">
        <ProfileCard user={user} />
      </div>

      <WireGuardCard userId={userId} />

      <Card className="mt-6">
        <CardHeader className="pb-2"><CardTitle className="text-base">Session history</CardTitle></CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ended</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead className="text-right">Upload</TableHead>
                <TableHead className="text-right">Download</TableHead>
                <TableHead className="text-right">Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {userEvents.length === 0 && (
                <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">No finished sessions yet</TableCell></TableRow>
              )}
              {userEvents.map((e, i) => (
                <TableRow key={i}>
                  <TableCell className="text-xs text-muted-foreground">{formatDate(e.ts)}</TableCell>
                  <TableCell className="text-sm">{formatDuration(e.session_time)}</TableCell>
                  <TableCell className="text-right text-xs">{formatBytes(e.in_octets)}</TableCell>
                  <TableCell className="text-right text-xs">{formatBytes(e.out_octets)}</TableCell>
                  <TableCell className="text-right text-sm font-medium">{formatBytes(e.total_octets)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <UserFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        user={user}
        onSubmit={async (p) => { await updateMut.mutateAsync(p); }}
        saving={updateMut.isPending}
      />
      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={confirm?.title ?? ""}
        description={confirm?.description}
        confirmLabel={confirm?.confirmLabel}
        onConfirm={() => { confirm?.action(); setConfirm(null); }}
      />
    </div>
  );
}
