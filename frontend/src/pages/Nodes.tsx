import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Cpu,
  KeyRound,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Server,
  Trash2,
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
import { Switch } from "@/components/ui/switch";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageHeader } from "@/components/widgets/PageHeader";
import { api, ApiError, type NodeCredentials, type NodeInfo } from "@/lib/api";
import { formatBytes, formatDuration, formatUptime } from "@/lib/format";
import { cn } from "@/lib/utils";

function StatusDot({ node }: { node: NodeInfo }) {
  const tone = !node.enabled
    ? "bg-muted-foreground"
    : node.online
      ? "bg-success"
      : "bg-destructive";
  return (
    <span className="relative flex h-2.5 w-2.5" title={node.online ? "Reporting" : "Not reporting"}>
      {node.online && node.enabled && (
        <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", tone)} />
      )}
      <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", tone)} />
    </span>
  );
}

function EngineChips({ node }: { node: NodeInfo }) {
  // Only meaningful for a node running an agent; node 1's engines are already
  // on the dashboard's own health widget.
  if (node.is_local) return <span className="text-xs text-muted-foreground">this panel server</span>;
  const engines = [
    { label: "L2TP", ok: node.xl2tpd_ok },
    { label: "IPsec", ok: node.ipsec_ok },
    { label: "SSTP", ok: node.accel_ppp_ok },
    { label: "WG", ok: node.wireguard_ok },
  ];
  return (
    <div className="flex flex-wrap gap-1">
      {engines.map((e) => (
        <span
          key={e.label}
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-medium",
            e.ok ? "bg-success/15 text-success" : "bg-muted text-muted-foreground",
          )}
          title={e.ok ? `${e.label} is running` : `${e.label} is not running on this node`}
        >
          {e.label}
        </span>
      ))}
    </div>
  );
}

/** Shown once after registration or rotation — the panel keeps no copy. */
function CredentialsDialog({
  creds,
  onClose,
}: {
  creds: NodeCredentials | null;
  onClose: () => void;
}) {
  const envFile = creds
    ? [
        `ATHENA_HUB=<panel-address>:50051`,
        `ATHENA_TOKEN=${creds.token}`,
        `ATHENA_WG_IFACE=wg-panel`,
        `ATHENA_CA=/etc/athena-agent/ca.crt`,
        `ATHENA_CERT=/etc/athena-agent/node.crt`,
        `ATHENA_KEY=/etc/athena-agent/node.key`,
      ].join("\n")
    : "";

  const download = () => {
    if (!creds) return;
    const files: [string, string][] = [
      [`node-${creds.id}-athena-agent.env`, envFile],
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
  };

  return (
    <Dialog open={!!creds} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Node {creds?.id} credentials</DialogTitle>
          <DialogDescription>
            Shown once. The panel stores only its own CA, never the node's private key,
            so save these now — if they are lost you can rotate to get a new set.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="text-xs">/etc/athena-agent.env</Label>
            <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-muted p-3 font-mono text-[11px] leading-relaxed">
              {envFile}
            </pre>
          </div>
          <div>
            <Label className="text-xs">Certificate + key</Label>
            <p className="mt-1 text-xs text-muted-foreground">
              Place <code>ca.crt</code>, <code>node.crt</code> and <code>node.key</code> in{" "}
              <code>/etc/athena-agent/</code> on the node, mode 0600 for the key.
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => { navigator.clipboard?.writeText(envFile); toast.success("Env copied"); }}>
            Copy env
          </Button>
          <Button onClick={download}>Download all 4 files</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function NodeFormDialog({
  open,
  node,
  onOpenChange,
  onSubmit,
  saving,
}: {
  open: boolean;
  node: NodeInfo | null;
  onOpenChange: (o: boolean) => void;
  onSubmit: (p: { name: string; address: string; note: string }) => Promise<void>;
  saving: boolean;
}) {
  const [name, setName] = React.useState("");
  const [address, setAddress] = React.useState("");
  const [note, setNote] = React.useState("");

  React.useEffect(() => {
    if (!open) return;
    setName(node?.name ?? "");
    setAddress(node?.address ?? "");
    setNote(node?.note ?? "");
  }, [open, node]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{node ? `Edit ${node.name}` : "Register a node"}</DialogTitle>
          <DialogDescription>
            {node
              ? "The entry address is what clients are pointed at. Change it when an address is burned — the node itself is unaffected."
              : "Registering mints a token and certificate for the node's agent. They are shown once."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="n-name">Name</Label>
            <Input id="n-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="de-fsn-1" />
          </div>
          <div>
            <Label htmlFor="n-addr">Entry address</Label>
            <Input
              id="n-addr"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              placeholder="88.218.18.91  (what clients connect to)"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Separate from wherever the agent dials the panel from, so a burned entry
              can be swapped without touching the node.
            </p>
          </div>
          <div>
            <Label htmlFor="n-note">Note</Label>
            <Input id="n-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            disabled={!name.trim() || saving}
            onClick={() => onSubmit({ name: name.trim(), address: address.trim(), note: note.trim() })}
          >
            {saving ? "Saving…" : node ? "Save" : "Register"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function Nodes() {
  const qc = useQueryClient();
  const { data: nodes = [], isLoading, isFetching, refetch } = useQuery({
    queryKey: ["nodes"],
    queryFn: api.listNodes,
    refetchInterval: 10000,
  });

  const [formOpen, setFormOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<NodeInfo | null>(null);
  const [creds, setCreds] = React.useState<NodeCredentials | null>(null);
  const [confirm, setConfirm] = React.useState<{
    title: string;
    description: string;
    confirmLabel: string;
    action: () => void;
  } | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["nodes"] });
  const fail = (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Request failed");

  const createMut = useMutation({
    mutationFn: (p: { name: string; address: string; note: string }) => api.createNode(p),
    onSuccess: (c) => { setFormOpen(false); setCreds(c); invalidate(); toast.success(`Node ${c.id} registered`); },
    onError: fail,
  });
  const updateMut = useMutation({
    mutationFn: ({ id, p }: { id: number; p: Record<string, unknown> }) => api.updateNode(id, p),
    onSuccess: () => { setFormOpen(false); setEditing(null); invalidate(); toast.success("Node updated"); },
    onError: fail,
  });
  const rotateMut = useMutation({
    mutationFn: (id: number) => api.rotateNode(id),
    onSuccess: (c) => { setCreds(c); invalidate(); toast.success("New credentials issued"); },
    onError: fail,
  });
  const deleteMut = useMutation({
    mutationFn: (id: number) => api.deleteNode(id),
    onSuccess: () => { invalidate(); toast.success("Node deleted"); },
    onError: fail,
  });

  return (
    <div>
      <PageHeader
        title="Nodes"
        description="Servers that terminate users · refreshes every 10s"
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} /> Refresh
            </Button>
            <Button size="sm" onClick={() => { setEditing(null); setFormOpen(true); }}>
              <Plus className="h-4 w-4" /> Register node
            </Button>
          </>
        }
      />

      {isLoading && <p className="py-10 text-center text-sm text-muted-foreground">Loading…</p>}

      <div className="grid gap-4 lg:grid-cols-2">
        {nodes.map((n) => (
          <Card key={n.id} className={cn(!n.enabled && "opacity-60")}>
            <CardContent className="p-4">
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  {n.is_local ? <Cpu className="h-5 w-5" /> : <Server className="h-5 w-5" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <StatusDot node={n} />
                    <span className="truncate font-semibold">{n.name}</span>
                    <Badge variant="outline" className="shrink-0 text-[10px]">#{n.id}</Badge>
                    {n.is_local && <Badge variant="secondary" className="shrink-0 text-[10px]">local</Badge>}
                    {!n.enabled && <Badge variant="destructive" className="shrink-0 text-[10px]">disabled</Badge>}
                  </div>
                  <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                    {n.address || "no entry address"}
                    {n.hostname && ` · ${n.hostname}`}
                    {n.agent_version && ` · agent ${n.agent_version}`}
                  </div>
                </div>

                {!n.is_local && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8">
                        <MoreHorizontal className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => { setEditing(n); setFormOpen(true); }}>
                        <Pencil /> Edit
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() =>
                          setConfirm({
                            title: `Rotate credentials for ${n.name}?`,
                            description:
                              "A new token and certificate are issued and the old ones stop working immediately. The node goes offline until its agent is reconfigured.",
                            confirmLabel: "Rotate",
                            action: () => rotateMut.mutate(n.id),
                          })
                        }
                      >
                        <KeyRound /> Rotate credentials
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-destructive focus:text-destructive"
                        onClick={() =>
                          setConfirm({
                            title: `Delete ${n.name}?`,
                            description:
                              n.sessions > 0
                                ? `${n.sessions} session(s) are still on this node. Disable it first and let them drain — deleting now is refused.`
                                : "The node is removed from the panel. Its agent will be rejected on the next connect.",
                            confirmLabel: "Delete",
                            action: () => deleteMut.mutate(n.id),
                          })
                        }
                      >
                        <Trash2 /> Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>

              <div className="mt-3 grid grid-cols-3 gap-2 border-t pt-3 text-center">
                <div>
                  <div className="text-lg font-bold tabular-nums">{n.sessions}</div>
                  <div className="text-[11px] text-muted-foreground">sessions</div>
                </div>
                <div>
                  <div className="text-lg font-bold tabular-nums">{n.ppp_count + n.wg_count}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {n.ppp_count} ppp · {n.wg_count} wg
                  </div>
                </div>
                <div>
                  <div className="text-lg font-bold tabular-nums">
                    {n.is_local ? "—" : n.online ? formatUptime(n.uptime_seconds) : "offline"}
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    {n.is_local
                      ? "always on"
                      : n.last_seen_seconds === null
                        ? "never reported"
                        : `seen ${formatDuration(n.last_seen_seconds)} ago`}
                  </div>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t pt-3">
                <EngineChips node={n} />
                {!n.is_local && n.mem_total_bytes > 0 && (
                  <span className="font-mono text-[11px] text-muted-foreground">
                    load {n.load1.toFixed(2)} · {formatBytes(n.mem_total_bytes - n.mem_available_bytes)}/
                    {formatBytes(n.mem_total_bytes)}
                  </span>
                )}
                {!n.is_local && (
                  <div className="flex items-center gap-2">
                    <Label htmlFor={`en-${n.id}`} className="text-xs text-muted-foreground">
                      Enabled
                    </Label>
                    <Switch
                      id={`en-${n.id}`}
                      checked={n.enabled}
                      onCheckedChange={(v) => updateMut.mutate({ id: n.id, p: { enabled: v } })}
                    />
                  </div>
                )}
              </div>

              {n.note && <p className="mt-2 text-xs text-muted-foreground">{n.note}</p>}
            </CardContent>
          </Card>
        ))}
      </div>

      <NodeFormDialog
        open={formOpen}
        node={editing}
        onOpenChange={setFormOpen}
        saving={createMut.isPending || updateMut.isPending}
        onSubmit={async (p) => {
          if (editing) await updateMut.mutateAsync({ id: editing.id, p });
          else await createMut.mutateAsync(p);
        }}
      />

      <CredentialsDialog creds={creds} onClose={() => setCreds(null)} />

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
