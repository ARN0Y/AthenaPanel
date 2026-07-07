import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Check,
  Cloud,
  Copy,
  Database,
  Download,
  Eye,
  EyeOff,
  KeyRound,
  Moon,
  Network,
  Send,
  Server,
  Sun,
  Trash2,
  Waypoints,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
import { PageHeader } from "@/components/widgets/PageHeader";
import { useAuth } from "@/hooks/useAuth";
import { useTheme } from "@/hooks/useTheme";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatBytes, formatUptime, relativeTime } from "@/lib/format";

function CopyField({ label, value, secret }: { label: string; value: string; secret?: boolean }) {
  const [show, setShow] = React.useState(!secret);
  const [copied, setCopied] = React.useState(false);
  const copy = () => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <div className="flex gap-2">
        <Input readOnly value={show ? value : "•".repeat(Math.min(value.length, 24))} className="font-mono text-sm" />
        {secret && (
          <Button variant="outline" size="icon" onClick={() => setShow((s) => !s)}>
            {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </Button>
        )}
        <Button variant="outline" size="icon" onClick={copy}>
          {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
        </Button>
      </div>
    </div>
  );
}

export function Settings() {
  const { theme, setTheme } = useTheme();
  const { isSuperadmin } = useAuth();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 10000 });
  const { data: sys } = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 8000 });
  const { data: outbounds = [] } = useQuery({ queryKey: ["outbounds"], queryFn: api.outbounds, refetchInterval: 15000 });

  // editable client-facing profile settings
  const [serverAddr, setServerAddr] = React.useState("");
  const [sstpAddr, setSstpAddr] = React.useState("");
  const [subAddr, setSubAddr] = React.useState("");
  const [l2tpOn, setL2tpOn] = React.useState(true);
  const [sstpOn, setSstpOn] = React.useState(false);
  React.useEffect(() => {
    if (data) {
      setServerAddr(data.server_address);
      setSstpAddr(data.sstp_address);
      setSubAddr(data.sub_address);
      setL2tpOn(data.l2tp_enabled);
      setSstpOn(data.sstp_enabled);
    }
  }, [data]);
  const settingsMut = useMutation({
    mutationFn: () => api.updateSettings({ server_address: serverAddr, sstp_address: sstpAddr, sub_address: subAddr, l2tp_enabled: l2tpOn, sstp_enabled: sstpOn }),
    onSuccess: () => { toast.success("Settings saved"); qc.invalidateQueries({ queryKey: ["settings"] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Save failed"),
  });

  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");

  const pwMut = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: (res) => { toast.success(res.detail); setCurrent(""); setNext(""); setConfirm(""); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Change failed"),
  });

  const submitPw = (e: React.FormEvent) => {
    e.preventDefault();
    if (next !== confirm) return toast.error("New passwords do not match");
    pwMut.mutate();
  };

  // ---- Backups (superadmin only) ----
  const { data: backupData, isFetching: backupsFetching } = useQuery({
    queryKey: ["backups"],
    queryFn: api.listBackups,
    enabled: isSuperadmin,
  });
  const createBackupMut = useMutation({
    mutationFn: api.createBackup,
    onSuccess: (b) => { toast.success(`Backup created (${formatBytes(b.size)})`); qc.invalidateQueries({ queryKey: ["backups"] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Backup failed"),
  });
  const deleteBackupMut = useMutation({
    mutationFn: (name: string) => api.deleteBackup(name),
    onSuccess: () => { toast.success("Backup deleted"); qc.invalidateQueries({ queryKey: ["backups"] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Delete failed"),
  });
  const [downloading, setDownloading] = React.useState<string | null>(null);
  const downloadBackup = async (name: string) => {
    setDownloading(name);
    try { await api.downloadBackup(name); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Download failed"); }
    finally { setDownloading(null); }
  };
  const telegramLinkMut = useMutation({
    mutationFn: () => api.telegramLink(),
    onSuccess: (r) => toast.success(`Telegram linked (chat ${r.chat_id})`),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Send /start to the bot first"),
  });
  const telegramSendMut = useMutation({
    mutationFn: (name: string) => api.telegramSend(name),
    onSuccess: () => toast.success("Sent to Telegram"),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Telegram send failed"),
  });

  return (
    <div>
      <PageHeader title="Settings" description="Server configuration, security and appearance" />

      <Tabs defaultValue="server">
        <TabsList>
          <TabsTrigger value="server"><Network className="h-4 w-4" /> Server</TabsTrigger>
          <TabsTrigger value="outbounds"><Waypoints className="h-4 w-4" /> Outbounds</TabsTrigger>
          <TabsTrigger value="security"><KeyRound className="h-4 w-4" /> Security</TabsTrigger>
          <TabsTrigger value="appearance"><Sun className="h-4 w-4" /> Appearance</TabsTrigger>
          <TabsTrigger value="about"><Server className="h-4 w-4" /> About</TabsTrigger>
          {isSuperadmin && <TabsTrigger value="backups"><Database className="h-4 w-4" /> Backups</TabsTrigger>}
        </TabsList>

        <TabsContent value="server" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Client profile</CardTitle>
              <CardDescription>Shown in each user's copy-able connection profile</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="srv">L2TP server address</Label>
                  <Input id="srv" value={serverAddr} onChange={(e) => setServerAddr(e.target.value)} placeholder="lttp.example.com" className="font-mono text-sm" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="sstp">SSTP server address</Label>
                  <Input id="sstp" value={sstpAddr} onChange={(e) => setSstpAddr(e.target.value)} placeholder="sstp.example.com" className="font-mono text-sm" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="sub">Subscription address</Label>
                  <Input id="sub" value={subAddr} onChange={(e) => setSubAddr(e.target.value)} placeholder="sb.example.com:2087" className="font-mono text-sm" />
                  <p className="text-xs text-muted-foreground">Host:port of the public sub page — builds each user's “Copy sub link”.</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-6">
                <label className="flex items-center gap-2 text-sm"><Switch checked={l2tpOn} onCheckedChange={setL2tpOn} /> L2TP/IPsec enabled</label>
                <label className="flex items-center gap-2 text-sm"><Switch checked={sstpOn} onCheckedChange={setSstpOn} /> SSTP enabled</label>
              </div>
              <Button onClick={() => settingsMut.mutate()} disabled={settingsMut.isPending}>Save profile settings</Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Connection details</CardTitle>
              <CardDescription>Read-only — configured in .env on the server</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <CopyField label="IPsec pre-shared key" value={data?.vpn_psk ?? ""} secret />
              <CopyField label="WAN interface" value={data?.wan_iface ?? ""} />
              <CopyField label="PPP gateway" value={data?.ppp_local_ip ?? ""} />
              <CopyField label="PPP address pool" value={data?.ppp_pool ?? ""} />
              <CopyField label="chap-secrets path" value={data?.chap_secrets ?? ""} />
              <CopyField label="Admin user" value={data?.admin_username ?? ""} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="outbounds" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Outbounds</CardTitle>
              <CardDescription>
                Where each user's traffic leaves the exit node. Choose one per user in the user form (default: Direct).
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 sm:grid-cols-2">
                {outbounds.map((o) => (
                  <div key={o.id} className="rounded-lg border p-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        {o.kind === "warp" ? (
                          <Cloud className="h-5 w-5 text-orange-400" />
                        ) : (
                          <ArrowUpRight className="h-5 w-5 text-muted-foreground" />
                        )}
                        <span className="font-medium">{o.name}</span>
                        {o.is_default && <Badge variant="secondary" className="text-[10px]">default</Badge>}
                      </div>
                      <Badge variant={o.status === "up" ? "success" : "destructive"}>
                        {o.status === "up" ? "Up" : "Down"}
                      </Badge>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">{o.description}</p>
                    <div className="mt-3 flex items-center gap-4 text-xs">
                      <span><span className="font-medium tabular-nums">{o.users}</span> <span className="text-muted-foreground">users</span></span>
                      {o.active !== null && (
                        <span><span className="font-medium tabular-nums">{o.active}</span> <span className="text-muted-foreground">routed now</span></span>
                      )}
                    </div>
                    <div className="mt-3 flex items-center justify-between rounded-md bg-muted/40 px-3 py-2">
                      <span className="text-xs text-muted-foreground">Egress IP</span>
                      <span className="font-mono text-xs">{o.egress_ip ?? "—"}</span>
                    </div>
                  </div>
                ))}
                {outbounds.length === 0 && (
                  <div className="col-span-full py-8 text-center text-sm text-muted-foreground">Loading outbounds…</div>
                )}
              </div>
              <p className="mt-4 text-xs text-muted-foreground">
                WARP exits on a Cloudflare IP; if it drops, its users fall back to Direct automatically. Additional outbounds can be added on the server.
              </p>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="security">
          <Card className="max-w-lg">
            <CardHeader>
              <CardTitle className="text-base">Change admin password</CardTitle>
              <CardDescription>
                Applies to the running session. Also update ADMIN_PASSWORD in /opt/vpn-panel/.env to persist across restarts.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={submitPw} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="cur">Current password</Label>
                  <Input id="cur" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="new">New password</Label>
                  <Input id="new" type="password" value={next} onChange={(e) => setNext(e.target.value)} required />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="cnf">Confirm new password</Label>
                  <Input id="cnf" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
                </div>
                <Button type="submit" disabled={pwMut.isPending}>Update password</Button>
              </form>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="appearance">
          <Card className="max-w-lg">
            <CardHeader>
              <CardTitle className="text-base">Theme</CardTitle>
              <CardDescription>Choose how the panel looks</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-3">
                {(["dark", "light"] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => setTheme(t)}
                    className={cn(
                      "flex items-center gap-3 rounded-lg border-2 p-4 transition-colors",
                      theme === t ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50",
                    )}
                  >
                    <div className={cn("flex h-10 w-10 items-center justify-center rounded-lg", t === "dark" ? "bg-slate-900 text-slate-100" : "bg-slate-100 text-slate-900")}>
                      {t === "dark" ? <Moon className="h-5 w-5" /> : <Sun className="h-5 w-5" />}
                    </div>
                    <div className="text-left">
                      <div className="font-medium capitalize">{t}</div>
                      <div className="text-xs text-muted-foreground">{t === "dark" ? "Easy on the eyes" : "Bright & clean"}</div>
                    </div>
                    {theme === t && <Check className="ml-auto h-5 w-5 text-primary" />}
                  </button>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="about">
          <div className="grid gap-6 md:grid-cols-2">
            <Card>
              <CardHeader><CardTitle className="text-base">Service health</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                {[
                  ["L2TP (xl2tpd, udp/1701)", health?.xl2tpd],
                  ["IPsec (Libreswan, udp/500)", health?.ipsec],
                  ["Database", health?.db],
                  ["Accounting log", health?.accounting_log],
                ].map(([label, ok]) => (
                  <div key={label as string} className="flex items-center justify-between">
                    <span className="text-sm">{label}</span>
                    <span className={cn("flex items-center gap-1.5 text-xs font-medium", ok ? "text-success" : "text-destructive")}>
                      <span className={cn("h-2 w-2 rounded-full", ok ? "bg-success" : "bg-destructive")} />
                      {ok ? "Running" : "Down"}
                    </span>
                  </div>
                ))}
                <Separator />
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">Backend uptime</span>
                  <span>{health ? formatUptime(health.uptime_seconds) : "—"}</span>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-base">Host</CardTitle></CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex justify-between"><span className="text-muted-foreground">Hostname</span><span className="font-mono">{sys?.hostname ?? "—"}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">Kernel</span><span className="font-mono text-xs">{sys?.kernel ?? "—"}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">Uptime</span><span>{sys ? formatUptime(sys.uptime_seconds) : "—"}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">Memory</span><span>{sys ? `${formatBytes(sys.mem_used)} / ${formatBytes(sys.mem_total)}` : "—"}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">Load avg</span><span className="font-mono">{sys ? `${sys.load_1} ${sys.load_5} ${sys.load_15}` : "—"}</span></div>
                <Separator />
                <div className="flex justify-between"><span className="text-muted-foreground">Panel version</span><span>v2.0.0</span></div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {isSuperadmin && (
          <TabsContent value="backups" className="space-y-6">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0">
                <div>
                  <CardTitle className="text-base">Database backups</CardTitle>
                  <CardDescription>
                    Compressed pg_dump snapshots. Auto-daily; the newest {backupData?.keep ?? 14} are kept.
                  </CardDescription>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => telegramLinkMut.mutate()} disabled={telegramLinkMut.isPending} title="Link this panel to the Telegram bot (send /start to it first)">
                    <Send className="h-4 w-4" /> {telegramLinkMut.isPending ? "Linking…" : "Connect Telegram"}
                  </Button>
                  <Button onClick={() => createBackupMut.mutate()} disabled={createBackupMut.isPending}>
                    <Database className="h-4 w-4" /> {createBackupMut.isPending ? "Backing up…" : "Backup now"}
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {!backupData || backupData.backups.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{backupsFetching ? "Loading…" : "No backups yet."}</p>
                ) : (
                  <div className="divide-y rounded-md border">
                    {backupData.backups.map((b) => (
                      <div key={b.name} className="flex items-center justify-between gap-3 px-3 py-2.5">
                        <div className="min-w-0">
                          <div className="truncate font-mono text-sm">{b.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {formatBytes(b.size)} · {relativeTime(b.created_at)}
                          </div>
                        </div>
                        <div className="flex shrink-0 gap-2">
                          <Button variant="outline" size="icon" onClick={() => telegramSendMut.mutate(b.name)} disabled={telegramSendMut.isPending} title="Send to Telegram">
                            <Send className="h-4 w-4 text-sky-400" />
                          </Button>
                          <Button variant="outline" size="icon" onClick={() => downloadBackup(b.name)} disabled={downloading === b.name} title="Download">
                            <Download className="h-4 w-4" />
                          </Button>
                          <Button variant="outline" size="icon" onClick={() => { if (window.confirm(`Delete ${b.name}?`)) deleteBackupMut.mutate(b.name); }} title="Delete">
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <p className="mt-4 text-xs text-muted-foreground">
                  Stored on the server at <span className="font-mono">{backupData?.dir ?? "/var/lib/vpn-panel/backups"}</span>.
                  Restore is a manual server operation (pg_restore with timescaledb_pre/post_restore).
                </p>
              </CardContent>
            </Card>
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
