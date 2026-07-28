import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Ban,
  CheckCircle2,
  Copy,
  Cpu,
  Download,
  KeyRound,
  MoreVertical,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Server,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageHeader } from "@/components/widgets/PageHeader";
import { api, ApiError, type NodeCredentials, type NodeInfo } from "@/lib/api";
import { formatBps, formatBytes, formatDuration, formatUptime } from "@/lib/format";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ pieces */

function Dot({ tone, pulse }: { tone: string; pulse?: boolean }) {
  return (
    <span className="relative flex h-2 w-2 shrink-0">
      {pulse && <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", tone)} />}
      <span className={cn("relative inline-flex h-2 w-2 rounded-full", tone)} />
    </span>
  );
}

/** One protocol's state on a node. Muted means "not running here", which for a
 *  WireGuard-only node is normal rather than a fault. */
function Engine({ label, ok, port }: { label: string; ok: boolean; port?: number }) {
  return (
    <span
      title={ok ? `${label} running${port ? ` on port ${port}` : ""}` : `${label} not running on this node`}
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium",
        ok ? "bg-success/15 text-success" : "bg-muted/70 text-muted-foreground/70",
      )}
    >
      <span className={cn("h-1 w-1 rounded-full", ok ? "bg-success" : "bg-muted-foreground/40")} />
      {label}
    </span>
  );
}

/** One customer-facing address.
 *
 *  Empty means two different things and the card must not blur them. On node 1
 *  it inherits the panel-wide setting, which is how every pre-node account keeps
 *  working. On any other node nothing is inherited — the panel-wide address is a
 *  relay pointing at the master, and a single host:port cannot forward to two
 *  backends — so empty there is a real gap that stops customers connecting. */
function ProxyLine({ label, value, isLocal }: { label: string; value: string; isLocal: boolean }) {
  return (
    <div className="flex items-baseline gap-1.5 truncate">
      <span className="shrink-0 text-muted-foreground/70">{label}</span>
      {value ? (
        <span className="truncate">{value}</span>
      ) : isLocal ? (
        <span className="truncate italic text-muted-foreground/50" title="Falls back to the panel-wide setting">
          panel default
        </span>
      ) : (
        <span
          className="truncate italic text-warning"
          title="Not set. Customers on this node have no address to connect to — the panel-wide one points at the master."
        >
          not set
        </span>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  sub,
  className,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className="min-w-0">
      <div className={cn("truncate text-sm font-semibold tabular-nums", className)}>{value}</div>
      <div className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      {sub && <div className="truncate text-[10px] text-muted-foreground/80">{sub}</div>}
    </div>
  );
}

function NodeCard({
  node,
  onEdit,
  onReconnect,
  onToggle,
  onRotate,
  onDelete,
}: {
  node: NodeInfo;
  onEdit: () => void;
  onReconnect: () => void;
  onToggle: () => void;
  onRotate: () => void;
  onDelete: () => void;
}) {
  const tone = !node.enabled ? "bg-muted-foreground" : node.online ? "bg-success" : "bg-destructive";
  const state = !node.enabled ? "Disabled" : node.online ? "Online" : "Not reporting";
  const total = node.rx_total_bytes + node.tx_total_bytes;

  return (
    <Card className={cn("overflow-hidden transition-colors", !node.enabled && "opacity-70")}>
      <CardContent className="p-0">
        {/* header */}
        <div className="flex items-start gap-3 p-4 pb-3">
          <div
            className={cn(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
              node.is_local ? "bg-primary/10 text-primary" : "bg-muted text-foreground/70",
            )}
          >
            {node.is_local ? <Cpu className="h-4.5 w-4.5" /> : <Server className="h-4.5 w-4.5" />}
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Dot tone={tone} pulse={node.online && node.enabled} />
              <span className="truncate font-semibold leading-none">{node.name}</span>
              <span className="text-[11px] text-muted-foreground">#{node.id}</span>
              {node.is_local && (
                <Badge variant="secondary" className="h-4 px-1.5 text-[9px] uppercase">
                  master
                </Badge>
              )}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[11px] text-muted-foreground">
              <span className={cn(!node.address && "italic opacity-60")} title="The node's own address — ours, never given to customers">
                {node.address || "address not set"}
              </span>
              {node.hostname && <span className="opacity-50">·</span>}
              {node.hostname && <span>{node.hostname}</span>}
              {node.agent_version && <span className="opacity-50">·</span>}
              {node.agent_version && <span className="text-primary/80">v{node.agent_version}</span>}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-[10px] font-medium",
                !node.enabled
                  ? "bg-muted text-muted-foreground"
                  : node.online
                    ? "bg-success/15 text-success"
                    : "bg-destructive/15 text-destructive",
              )}
            >
              {state}
            </span>
            {!node.is_local && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-7 w-7">
                    <MoreVertical className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-48">
                  <DropdownMenuItem onClick={onEdit}><Pencil /> Edit</DropdownMenuItem>
                  <DropdownMenuItem onClick={onReconnect}><RefreshCw /> Reconnect</DropdownMenuItem>
                  <DropdownMenuItem onClick={onToggle}>
                    {node.enabled ? <><Ban /> Disable</> : <><CheckCircle2 /> Enable</>}
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={onRotate}><KeyRound /> Rotate credentials</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem className="text-destructive focus:text-destructive" onClick={onDelete}>
                    <Trash2 /> Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </div>

        {/* live throughput */}
        <div className="grid grid-cols-2 gap-px bg-border">
          <div className="flex items-center gap-2 bg-card px-4 py-2.5">
            <ArrowDownToLine className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--chart-rx))]" />
            <div className="min-w-0">
              <div className="truncate font-mono text-sm font-semibold tabular-nums text-[hsl(var(--chart-rx))]">
                {formatBps(node.tx_rate_bps)}
              </div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">download</div>
            </div>
          </div>
          <div className="flex items-center gap-2 bg-card px-4 py-2.5">
            <ArrowUpFromLine className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--chart-tx))]" />
            <div className="min-w-0">
              <div className="truncate font-mono text-sm font-semibold tabular-nums text-[hsl(var(--chart-tx))]">
                {formatBps(node.rx_rate_bps)}
              </div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">upload</div>
            </div>
          </div>
        </div>

        {/* counters */}
        <div className="grid grid-cols-3 gap-3 border-t px-4 py-3">
          {/* One number, one breakdown. They used to be two metrics reading two
              different sources, which meant the card could show "4 sessions"
              next to "0 tunnels" and look broken. */}
          <Metric
            label="sessions"
            value={node.sessions}
            sub={node.wg_count > 0 ? `${node.ppp_count} ppp · ${node.wg_count} wg` : undefined}
          />
          <Metric
            label="transferred"
            value={formatBytes(total)}
            sub={`↓${formatBytes(node.tx_total_bytes)} · ↑${formatBytes(node.rx_total_bytes)}`}
          />
          <Metric
            label={node.is_local ? "uptime" : node.online ? "uptime" : "last seen"}
            value={
              node.is_local
                ? "—"
                : node.online
                  ? formatUptime(node.uptime_seconds)
                  : node.last_seen_seconds === null
                    ? "never"
                    : formatDuration(node.last_seen_seconds)
            }
            sub={node.is_local ? "always on" : node.online ? `seen ${node.last_seen_seconds}s ago` : undefined}
          />
        </div>

        {/* engines + host */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t bg-muted/30 px-4 py-2.5">
          <div className="flex flex-wrap gap-1">
            <Engine label="L2TP" ok={node.xl2tpd_ok} port={node.l2tp_port} />
            <Engine label="IPsec" ok={node.ipsec_ok} />
            <Engine label="SSTP" ok={node.accel_ppp_ok} port={node.sstp_port} />
            <Engine label="WG" ok={node.wireguard_ok} port={node.wg_port} />
          </div>
          {node.mem_total_bytes > 0 && (
            <span className="font-mono text-[10px] text-muted-foreground">
              load {node.load1.toFixed(2)} ·{" "}
              {formatBytes(node.mem_total_bytes - node.mem_available_bytes)}/{formatBytes(node.mem_total_bytes)}
            </span>
          )}
        </div>

        <div className="border-t px-4 py-2.5">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
            external proxy — what customers dial
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono text-[11px]">
            <ProxyLine label="L2TP" value={node.ext_l2tp_address} isLocal={node.is_local} />
            <ProxyLine label="raw" value={node.ext_l2tp_raw_address} isLocal={node.is_local} />
            <ProxyLine label="SSTP" value={node.ext_sstp_address} isLocal={node.is_local} />
            <ProxyLine label="WG" value={node.ext_wg_endpoint} isLocal={node.is_local} />
          </div>
        </div>

        {node.note && (
          <p className="border-t px-4 py-2 text-[11px] text-muted-foreground">{node.note}</p>
        )}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------ create wizard + install */

function InstallInstructions({ creds, hub }: { creds: NodeCredentials; hub: string }) {
  const env = [
    `ATHENA_HUB=${hub}`,
    `ATHENA_TOKEN=${creds.token}`,
    `ATHENA_WG_IFACE=wg-panel`,
    `ATHENA_CA=/etc/athena-agent/ca.crt`,
    `ATHENA_CERT=/etc/athena-agent/node.crt`,
    `ATHENA_KEY=/etc/athena-agent/node.key`,
  ].join("\n");

  const download = () => {
    const files: [string, string][] = [
      [`node-${creds.id}-athena-agent.env`, env],
      [`node-${creds.id}-ca.crt`, creds.ca_cert],
      [`node-${creds.id}-node.crt`, creds.client_cert],
      [`node-${creds.id}-node.key`, creds.client_key],
    ];
    for (const [name, body] of files) {
      const url = URL.createObjectURL(new Blob([body], { type: "text/plain" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
    }
    toast.success("4 files downloaded");
  };

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-xs">
        Shown once. The panel keeps only its own CA and never the node's private key, so save
        these now — if they are lost, rotate the node to get a new set.
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between">
          <Label className="text-xs">/etc/athena-agent.env</Label>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-[11px]"
            onClick={() => { navigator.clipboard?.writeText(env); toast.success("Copied"); }}
          >
            <Copy className="h-3 w-3" /> Copy
          </Button>
        </div>
        <pre className="max-h-36 overflow-auto rounded-md bg-muted p-3 font-mono text-[11px] leading-relaxed">
          {env}
        </pre>
      </div>
      <div>
        <Label className="text-xs">Then, on the node</Label>
        <pre className="mt-1 overflow-auto rounded-md bg-muted p-3 font-mono text-[11px] leading-relaxed">
{`# place ca.crt, node.crt and node.key in /etc/athena-agent/ (key mode 0600)
bash node-bootstrap.sh`}
        </pre>
      </div>
      <Button variant="outline" className="w-full" onClick={download}>
        <Download className="h-4 w-4" /> Download all 4 files
      </Button>
    </div>
  );
}

function CreateNodeDialog({
  open,
  onOpenChange,
  hub,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  hub: string;
}) {
  const qc = useQueryClient();
  const [name, setName] = React.useState("");
  const [address, setAddress] = React.useState("");
  const [note, setNote] = React.useState("");
  const [wgPort, setWgPort] = React.useState(51820);
  const [sstpPort, setSstpPort] = React.useState(443);
  const [l2tpPort, setL2tpPort] = React.useState(1701);
  const [advanced, setAdvanced] = React.useState(false);
  const [creds, setCreds] = React.useState<NodeCredentials | null>(null);

  React.useEffect(() => {
    if (open) {
      setName(""); setAddress(""); setNote("");
      setWgPort(51820); setSstpPort(443); setL2tpPort(1701);
      setAdvanced(false); setCreds(null);
    }
  }, [open]);

  // Once the node exists, poll until its agent checks in. This replaces a
  // "check status" button: in this design the node dials the panel, so there is
  // nothing to probe until the agent is actually running.
  const { data: nodes = [] } = useQuery({
    queryKey: ["nodes"],
    queryFn: api.listNodes,
    refetchInterval: creds ? 3000 : false,
    enabled: !!creds,
  });
  const live = creds ? nodes.find((n) => n.id === creds.id) : undefined;
  const connected = !!live?.online;

  const createMut = useMutation({
    mutationFn: () =>
      api.createNode({
        name: name.trim(), address: address.trim(), note: note.trim(),
        wg_port: wgPort, sstp_port: sstpPort, l2tp_port: l2tpPort,
      }),
    onSuccess: (c) => { setCreds(c); qc.invalidateQueries({ queryKey: ["nodes"] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not register the node"),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Server className="h-5 w-5" /> {creds ? `Install node ${creds.id}` : "Register a node"}
          </DialogTitle>
          <DialogDescription>
            {creds
              ? "The node exists. Install the agent on the server and it will appear here."
              : "The node dials the panel, so it is registered first and installed second. Only the name is required — everything else can be filled in later."}
          </DialogDescription>
        </DialogHeader>

        {!creds ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Label htmlFor="nd-name">Node name <span className="text-destructive">*</span></Label>
              <Input
                id="nd-name" value={name} onChange={(e) => setName(e.target.value)}
                placeholder="de-fsn-1" autoFocus
              />
              <p className="mt-1 text-[11px] text-muted-foreground">How you will recognise it. Location plus a number works well.</p>
            </div>

            <div className="sm:col-span-2">
              <Label htmlFor="nd-addr">Node address <span className="text-muted-foreground">(optional)</span></Label>
              <Input
                id="nd-addr" value={address} onChange={(e) => setAddress(e.target.value)}
                placeholder="91.98.237.167"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">
                The server itself, for your reference. Customers never reach it directly — they
                dial the relay in front of it, which is configured after the node is up.
              </p>
            </div>

            <div className="sm:col-span-2">
              <Label htmlFor="nd-note">Note</Label>
              <Input id="nd-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Hetzner FSN, 1 Gbps" />
            </div>

            <div className="sm:col-span-2">
              <button
                type="button"
                onClick={() => setAdvanced((a) => !a)}
                className="text-xs font-medium text-primary hover:underline"
              >
                {advanced ? "Hide" : "Show"} service ports
              </button>
              {advanced && (
                <div className="mt-2 grid grid-cols-3 gap-3 rounded-md border p-3">
                  <div>
                    <Label htmlFor="nd-wg" className="text-xs">WireGuard</Label>
                    <Input id="nd-wg" type="number" value={wgPort} onChange={(e) => setWgPort(+e.target.value)} />
                  </div>
                  <div>
                    <Label htmlFor="nd-sstp" className="text-xs">SSTP</Label>
                    <Input id="nd-sstp" type="number" value={sstpPort} onChange={(e) => setSstpPort(+e.target.value)} />
                  </div>
                  <div>
                    <Label htmlFor="nd-l2tp" className="text-xs">L2TP</Label>
                    <Input id="nd-l2tp" type="number" value={l2tpPort} onChange={(e) => setL2tpPort(+e.target.value)} />
                  </div>
                  <p className="col-span-3 text-[11px] text-muted-foreground">
                    Per node, because a port that is blocked in one country is fine in another.
                    These feed the configs handed to clients.
                  </p>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div
              className={cn(
                "flex items-center gap-3 rounded-md border p-3",
                connected ? "border-success/40 bg-success/10" : "border-border bg-muted/40",
              )}
            >
              <Dot tone={connected ? "bg-success" : "bg-muted-foreground"} pulse={!connected} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">
                  {connected ? "Agent connected" : "Waiting for the agent to connect…"}
                </div>
                <div className="text-[11px] text-muted-foreground">
                  {connected
                    ? `${live?.hostname || "node"} · agent v${live?.agent_version}`
                    : "This updates by itself once the agent on the node dials in."}
                </div>
              </div>
              {connected && <CheckCircle2 className="h-5 w-5 text-success" />}
            </div>
            <InstallInstructions creds={creds} hub={hub} />
          </div>
        )}

        <DialogFooter>
          {!creds ? (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button disabled={!name.trim() || createMut.isPending} onClick={() => createMut.mutate()}>
                {createMut.isPending ? "Registering…" : "Register and show install steps"}
              </Button>
            </>
          ) : (
            <Button onClick={() => onOpenChange(false)}>{connected ? "Done" : "Close"}</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditNodeDialog({
  node,
  onOpenChange,
}: {
  node: NodeInfo | null;
  onOpenChange: (o: boolean) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = React.useState("");
  const [address, setAddress] = React.useState("");
  const [note, setNote] = React.useState("");
  const [wgPort, setWgPort] = React.useState(51820);
  const [sstpPort, setSstpPort] = React.useState(443);
  const [l2tpPort, setL2tpPort] = React.useState(1701);
  const [extL2tp, setExtL2tp] = React.useState("");
  const [extRaw, setExtRaw] = React.useState("");
  const [extSstp, setExtSstp] = React.useState("");
  const [extWg, setExtWg] = React.useState("");

  React.useEffect(() => {
    if (!node) return;
    setName(node.name); setAddress(node.address); setNote(node.note);
    setWgPort(node.wg_port); setSstpPort(node.sstp_port); setL2tpPort(node.l2tp_port);
    setExtL2tp(node.ext_l2tp_address); setExtRaw(node.ext_l2tp_raw_address);
    setExtSstp(node.ext_sstp_address); setExtWg(node.ext_wg_endpoint);
  }, [node]);

  const mut = useMutation({
    mutationFn: () =>
      api.updateNode(node!.id, {
        name: name.trim(), address: address.trim(), note: note.trim(),
        wg_port: wgPort, sstp_port: sstpPort, l2tp_port: l2tpPort,
        ext_l2tp_address: extL2tp.trim(), ext_l2tp_raw_address: extRaw.trim(),
        ext_sstp_address: extSstp.trim(), ext_wg_endpoint: extWg.trim(),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nodes"] });
      toast.success("Node updated");
      onOpenChange(false);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Update failed"),
  });

  return (
    <Dialog open={!!node} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit {node?.name}</DialogTitle>
          <DialogDescription>
            External proxy changes apply to profiles generated from now on. Sessions already
            connected are untouched.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="e-name">Name</Label>
              <Input id="e-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="e-addr">Node address</Label>
              <Input id="e-addr" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="91.98.237.167" />
              <p className="mt-1 text-[11px] text-muted-foreground">
                The server itself. Ours, never handed to a customer.
              </p>
            </div>
            <div className="sm:col-span-2">
              <Label htmlFor="e-note">Note</Label>
              <Input id="e-note" value={note} onChange={(e) => setNote(e.target.value)} />
            </div>
          </div>

          <div className="rounded-md border p-3">
            <div className="mb-1 text-sm font-medium">External proxy</div>
            <p className="mb-3 text-[11px] text-muted-foreground">
              What customers actually dial: the relay in front of this node, not the node itself.
              Per protocol, because raw L2TP needs its own entry — IPsec is negotiated before the
              user is known, so the two modes cannot share an address. An empty field inherits the
              panel-wide setting.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="e-ext-l2tp" className="text-xs">L2TP / IPsec</Label>
                <Input id="e-ext-l2tp" value={extL2tp} onChange={(e) => setExtL2tp(e.target.value)} placeholder="lttp.topmeli.com" />
              </div>
              <div>
                <Label htmlFor="e-ext-raw" className="text-xs">L2TP raw (no IPsec)</Label>
                <Input id="e-ext-raw" value={extRaw} onChange={(e) => setExtRaw(e.target.value)} placeholder="lttpraw.topmeli.com" />
              </div>
              <div>
                <Label htmlFor="e-ext-sstp" className="text-xs">SSTP</Label>
                <Input id="e-ext-sstp" value={extSstp} onChange={(e) => setExtSstp(e.target.value)} placeholder="sstp.topmeli.com" />
              </div>
              <div>
                <Label htmlFor="e-ext-wg" className="text-xs">WireGuard</Label>
                <Input id="e-ext-wg" value={extWg} onChange={(e) => setExtWg(e.target.value)} placeholder="wg.topmeli.com:51820" />
              </div>
            </div>
          </div>

          <div className="rounded-md border p-3">
            <div className="mb-1 text-sm font-medium">Service ports on the node</div>
            <p className="mb-3 text-[11px] text-muted-foreground">
              What the node itself listens on. A port blocked in one country is fine in another.
            </p>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <Label htmlFor="e-wg" className="text-xs">WireGuard</Label>
                <Input id="e-wg" type="number" value={wgPort} onChange={(e) => setWgPort(+e.target.value)} />
              </div>
              <div>
                <Label htmlFor="e-sstp" className="text-xs">SSTP</Label>
                <Input id="e-sstp" type="number" value={sstpPort} onChange={(e) => setSstpPort(+e.target.value)} />
              </div>
              <div>
                <Label htmlFor="e-l2tp" className="text-xs">L2TP</Label>
                <Input id="e-l2tp" type="number" value={l2tpPort} onChange={(e) => setL2tpPort(+e.target.value)} />
              </div>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={mut.isPending} onClick={() => mut.mutate()}>
            {mut.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* -------------------------------------------------------------------- page */

export function Nodes() {
  const qc = useQueryClient();
  const { data: nodes = [], isLoading, isFetching, refetch } = useQuery({
    queryKey: ["nodes"],
    queryFn: api.listNodes,
    refetchInterval: 5000,
  });

  const [query, setQuery] = React.useState("");
  const [createOpen, setCreateOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<NodeInfo | null>(null);
  const [creds, setCreds] = React.useState<NodeCredentials | null>(null);
  const [confirm, setConfirm] = React.useState<{
    title: string; description: string; confirmLabel: string; action: () => void;
  } | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["nodes"] });
  const fail = (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Request failed");

  const updateMut = useMutation({
    mutationFn: ({ id, p }: { id: number; p: Record<string, unknown> }) => api.updateNode(id, p),
    onSuccess: () => { invalidate(); toast.success("Node updated"); },
    onError: fail,
  });
  const reconnectMut = useMutation({
    mutationFn: (id: number) => api.reconnectNode(id),
    onSuccess: () => toast.success("Reconnect requested — the agent redials within seconds"),
    onError: fail,
  });
  const rotateMut = useMutation({
    mutationFn: (id: number) => api.rotateNode(id),
    onSuccess: (c) => { setCreds(c); invalidate(); },
    onError: fail,
  });
  const deleteMut = useMutation({
    mutationFn: (id: number) => api.deleteNode(id),
    onSuccess: () => { invalidate(); toast.success("Node deleted"); },
    onError: fail,
  });

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return nodes;
    return nodes.filter((n) =>
      [n.name, n.address, n.hostname, n.note, String(n.id)].some((f) => (f || "").toLowerCase().includes(q)),
    );
  }, [nodes, query]);

  const online = nodes.filter((n) => n.online && n.enabled).length;
  const totalRx = nodes.reduce((a, n) => a + n.rx_rate_bps, 0);
  const totalTx = nodes.reduce((a, n) => a + n.tx_rate_bps, 0);
  const totalBytes = nodes.reduce((a, n) => a + n.rx_total_bytes + n.tx_total_bytes, 0);
  // The address agents are told to dial. Shown in the install steps; the panel
  // does not know its own public address, so this is a best-effort default the
  // operator can correct.
  const hub = `${window.location.hostname}:50051`;

  return (
    <div>
      <PageHeader
        title="Nodes"
        description="Servers that terminate users · refreshes every 5s"
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} /> Refresh
            </Button>
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" /> Register node
            </Button>
          </>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-4">
        <Card>
          <CardContent className="p-3">
            <div className="text-xl font-bold tabular-nums">
              {online}
              <span className="text-sm font-normal text-muted-foreground">/{nodes.length}</span>
            </div>
            <div className="text-[11px] text-muted-foreground">nodes online</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3">
            <div className="text-xl font-bold tabular-nums text-[hsl(var(--chart-rx))]">{formatBps(totalTx)}</div>
            <div className="text-[11px] text-muted-foreground">download, all nodes</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3">
            <div className="text-xl font-bold tabular-nums text-[hsl(var(--chart-tx))]">{formatBps(totalRx)}</div>
            <div className="text-[11px] text-muted-foreground">upload, all nodes</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3">
            <div className="text-xl font-bold tabular-nums">{formatBytes(totalBytes)}</div>
            <div className="text-[11px] text-muted-foreground">transferred, all nodes</div>
          </CardContent>
        </Card>
      </div>

      <div className="relative mb-4 max-w-md">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          placeholder="Search name, address or host…"
          className="pl-9 pr-9"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button
            type="button"
            onClick={() => setQuery("")}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {isLoading && <p className="py-10 text-center text-sm text-muted-foreground">Loading…</p>}
      {!isLoading && filtered.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center">
            <Server className="mx-auto h-8 w-8 text-muted-foreground/40" />
            <p className="mt-3 text-sm text-muted-foreground">
              {query ? `No node matches “${query}”.` : "No nodes yet."}
            </p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        {filtered.map((n) => (
          <NodeCard
            key={n.id}
            node={n}
            onEdit={() => setEditing(n)}
            onReconnect={() => reconnectMut.mutate(n.id)}
            onToggle={() => updateMut.mutate({ id: n.id, p: { enabled: !n.enabled } })}
            onRotate={() =>
              setConfirm({
                title: `Rotate credentials for ${n.name}?`,
                description:
                  "A new token and certificate are issued and the old ones stop working at once. The node stays offline until its agent is reconfigured.",
                confirmLabel: "Rotate",
                action: () => rotateMut.mutate(n.id),
              })
            }
            onDelete={() =>
              setConfirm({
                title: `Delete ${n.name}?`,
                description:
                  n.sessions > 0
                    ? `${n.sessions} session(s) are still on this node. Deleting is refused while they are live — disable it and let them drain first.`
                    : "The node is removed and its agent is rejected on the next connect.",
                confirmLabel: "Delete",
                action: () => deleteMut.mutate(n.id),
              })
            }
          />
        ))}
      </div>

      <CreateNodeDialog open={createOpen} onOpenChange={setCreateOpen} hub={hub} />
      <EditNodeDialog node={editing} onOpenChange={(o) => !o && setEditing(null)} />

      <Dialog open={!!creds} onOpenChange={(o) => !o && setCreds(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>New credentials for node {creds?.id}</DialogTitle>
            <DialogDescription>The previous token and certificate no longer work.</DialogDescription>
          </DialogHeader>
          {creds && <InstallInstructions creds={creds} hub={hub} />}
          <DialogFooter>
            <Button onClick={() => setCreds(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
