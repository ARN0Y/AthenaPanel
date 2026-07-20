import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpDown,
  Ban,
  CheckCircle2,
  Cloud,
  Copy,
  Download,
  Link2,
  MoreHorizontal,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/widgets/PageHeader";
import { UserStatusBadge } from "@/components/widgets/StatusBadge";
import { QuotaBar } from "@/components/widgets/QuotaBar";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { UserFormDialog } from "@/components/UserFormDialog";
import { api, ApiError, type BulkActionType, type User, type UserPayload } from "@/lib/api";
import { formatDate, formatRate, relativeTime } from "@/lib/format";
import { copyText } from "@/lib/clipboard";
import { isRawMode, profileText } from "@/lib/profile";
import { useAuth } from "@/hooks/useAuth";

type SortKey = "created_at" | "username" | "used_bytes" | "last_seen" | "expires_at" | "rate_down_kbps";
type StatusFilter = "all" | "online" | "offline" | "disabled" | "expired";
const PAGE_SIZE = 12;

function matchesStatus(u: User, f: StatusFilter): boolean {
  switch (f) {
    case "online": return u.online;
    case "offline": return !u.online && u.is_active && !u.is_expired;
    case "disabled": return !u.is_active;
    case "expired": return u.is_expired;
    default: return true;
  }
}

export function Users() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { isSuperadmin } = useAuth();
  const { data: users = [], isLoading } = useQuery({
    queryKey: ["users"],
    queryFn: api.listUsers,
    refetchInterval: 8000,
  });
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.settings });

  const [search, setSearch] = React.useState("");
  const [status, setStatus] = React.useState<StatusFilter>("all");
  const [sortKey, setSortKey] = React.useState<SortKey>("created_at");
  const [sortDir, setSortDir] = React.useState<"asc" | "desc">("desc");
  const [page, setPage] = React.useState(0);
  const [selected, setSelected] = React.useState<Set<number>>(new Set());
  const [formOpen, setFormOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<User | null>(null);
  const [confirm, setConfirm] = React.useState<{
    title: string;
    description: string;
    confirmLabel: string;
    action: () => void;
  } | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["users"] });

  const createMut = useMutation({
    mutationFn: (p: UserPayload) => api.createUser(p),
    onSuccess: () => { toast.success("User created"); setFormOpen(false); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Create failed"),
  });
  const updateMut = useMutation({
    mutationFn: ({ id, p }: { id: number; p: UserPayload }) => api.updateUser(id, p),
    onSuccess: () => { toast.success("User updated"); setFormOpen(false); setEditing(null); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Update failed"),
  });
  const deleteMut = useMutation({
    mutationFn: (id: number) => api.deleteUser(id),
    onSuccess: () => { toast.success("User deleted"); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Delete failed"),
  });
  const toggleMut = useMutation({
    mutationFn: (id: number) => api.toggleUser(id),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Toggle failed"),
  });
  const resetMut = useMutation({
    mutationFn: (id: number) => api.resetQuota(id),
    onSuccess: () => { toast.success("Quota reset"); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Reset failed"),
  });
  const bulkMut = useMutation({
    mutationFn: ({ ids, action }: { ids: number[]; action: BulkActionType }) => api.bulk(ids, action),
    onSuccess: (res) => { toast.success(`${res.action}: ${res.affected.length} user(s)`); setSelected(new Set()); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Bulk action failed"),
  });

  const filtered = React.useMemo(() => {
    let list = users.filter((u) => matchesStatus(u, status));
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((u) => u.username.toLowerCase().includes(q) || u.note.toLowerCase().includes(q));
    }
    list = [...list].sort((a, b) => {
      let av: number | string = "";
      let bv: number | string = "";
      switch (sortKey) {
        case "created_at": av = Date.parse(a.created_at); bv = Date.parse(b.created_at); break;
        case "username": av = a.username; bv = b.username; break;
        case "used_bytes": av = a.used_bytes; bv = b.used_bytes; break;
        case "rate_down_kbps": av = a.rate_down_kbps; bv = b.rate_down_kbps; break;
        case "last_seen": av = a.last_seen ? Date.parse(a.last_seen) : 0; bv = b.last_seen ? Date.parse(b.last_seen) : 0; break;
        case "expires_at": av = a.expires_at ? Date.parse(a.expires_at) : Infinity; bv = b.expires_at ? Date.parse(b.expires_at) : Infinity; break;
      }
      const cmp = typeof av === "string" ? av.localeCompare(bv as string) : (av as number) - (bv as number);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return list;
  }, [users, status, search, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  React.useEffect(() => { setPage(0); }, [search, status, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };

  const pageIds = current.map((u) => u.id);
  const allChecked = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const toggleAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (allChecked) pageIds.forEach((id) => next.delete(id));
      else pageIds.forEach((id) => next.add(id));
      return next;
    });
  const toggleOne = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const exportCsv = () => {
    const rows = [
      ["username", "status", "used_bytes", "quota_bytes", "down_kbps", "up_kbps", "expires_at", "last_seen", "note"],
      ...filtered.map((u) => [
        u.username,
        u.is_active ? (u.is_expired ? "expired" : "active") : "disabled",
        u.used_bytes, u.quota_bytes, u.rate_down_kbps, u.rate_up_kbps,
        u.expires_at ?? "", u.last_seen ?? "", u.note.replace(/,/g, " "),
      ]),
    ];
    const csv = rows.map((r) => r.join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "vpn-users.csv";
    a.click();
  };

  const copyProfile = (u: User) => {
    const text = profileText(u, settings);
    if (!text) {
      // Distinguish "nothing enabled" from "raw user, but no raw host set" —
      // the second is a one-field fix in Settings, so say so.
      const raw = isRawMode(u) && settings?.l2tp_enabled && !settings?.l2tp_raw_address?.trim();
      toast.error(raw ? "Set “L2TP raw address” in Settings first" : "No protocol enabled (Settings)");
      return;
    }
    copyText(text).then(() => toast.success(`Profile for ${u.username} copied`)).catch(() => toast.error("Copy failed"));
  };

  const subLink = (u: User): string | null => {
    const host = settings?.sub_address?.trim();
    if (!host || !u.sub_token) return null;
    const base = /^https?:\/\//i.test(host) ? host : `http://${host}`;
    return `${base.replace(/\/$/, "")}/sub/${u.sub_token}`;
  };
  const copySubLink = (u: User) => {
    const url = subLink(u);
    if (!url) { toast.error("Set the subscription address in Settings"); return; }
    copyText(url).then(() => toast.success(`Sub link for ${u.username} copied`)).catch(() => toast.error("Copy failed"));
  };

  // header stat strip
  const onlineCount = users.filter((u) => u.online).length;
  const activeCount = users.filter((u) => u.is_active && !u.is_expired).length;
  const expiredCount = users.filter((u) => u.is_expired).length;
  const nearQuota = users.filter((u) => u.quota_bytes > 0 && u.used_bytes >= 0.8 * u.quota_bytes).length;

  const ids = Array.from(selected);
  const SortHead = ({ k, children, className }: { k: SortKey; children: React.ReactNode; className?: string }) => (
    <TableHead className={className}>
      <button className="inline-flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort(k)}>
        {children}
        <ArrowUpDown className={`h-3 w-3 ${sortKey === k ? "text-primary" : "opacity-40"}`} />
      </button>
    </TableHead>
  );

  return (
    <div>
      <PageHeader
        title="Users"
        description="Create, limit and monitor VPN accounts"
        actions={
          <>
            <Button variant="outline" size="sm" onClick={exportCsv}>
              <Download className="h-4 w-4" /> Export
            </Button>
            <Button size="sm" onClick={() => { setEditing(null); setFormOpen(true); }}>
              <Plus className="h-4 w-4" /> New user
            </Button>
          </>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: "Total users", value: users.length, cls: "text-foreground" },
          { label: "Online now", value: onlineCount, cls: "text-success" },
          { label: "Near / over quota", value: nearQuota, cls: nearQuota ? "text-warning" : "text-foreground" },
          { label: "Expired", value: expiredCount, cls: expiredCount ? "text-destructive" : "text-foreground" },
        ].map((s) => (
          <Card key={s.label}>
            <CardContent className="p-4">
              <div className={`text-2xl font-bold tabular-nums ${s.cls}`}>{s.value}</div>
              <div className="text-xs text-muted-foreground">{s.label}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center gap-3 border-b p-4">
            <span className="text-xs text-muted-foreground">{activeCount} active</span>
            <div className="relative min-w-[200px] flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search username or note…"
                className="pl-9"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <Select value={status} onValueChange={(v) => setStatus(v as StatusFilter)}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                <SelectItem value="online">Online</SelectItem>
                <SelectItem value="offline">Offline</SelectItem>
                <SelectItem value="disabled">Disabled</SelectItem>
                <SelectItem value="expired">Expired</SelectItem>
              </SelectContent>
            </Select>
            <Badge variant="outline" className="ml-auto">{filtered.length} users</Badge>
          </div>

          {selected.size > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-4 py-2.5">
              <span className="text-sm font-medium">{selected.size} selected</span>
              <div className="ml-auto flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => bulkMut.mutate({ ids, action: "enable" })}>
                  <CheckCircle2 className="h-4 w-4" /> Enable
                </Button>
                <Button variant="outline" size="sm" onClick={() => bulkMut.mutate({ ids, action: "disable" })}>
                  <Ban className="h-4 w-4" /> Disable
                </Button>
                <Button variant="outline" size="sm" onClick={() => bulkMut.mutate({ ids, action: "reset-quota" })}>
                  <RotateCcw className="h-4 w-4" /> Reset quota
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() =>
                    setConfirm({
                      title: `Delete ${selected.size} user(s)?`,
                      description: "They will be removed from chap-secrets and disconnected.",
                      confirmLabel: "Delete",
                      action: () => bulkMut.mutate({ ids, action: "delete" }),
                    })
                  }
                >
                  <Trash2 className="h-4 w-4" /> Delete
                </Button>
              </div>
            </div>
          )}

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox checked={allChecked} onCheckedChange={toggleAll} />
                </TableHead>
                <SortHead k="username">User</SortHead>
                <TableHead>Status</TableHead>
                <SortHead k="used_bytes" className="min-w-[170px]">Quota</SortHead>
                <SortHead k="rate_down_kbps">Speed ↓/↑</SortHead>
                <SortHead k="last_seen">Last seen</SortHead>
                <SortHead k="expires_at">Expiry</SortHead>
                <SortHead k="created_at">Created</SortHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading && (
                <TableRow><TableCell colSpan={9} className="py-10 text-center text-muted-foreground">Loading…</TableCell></TableRow>
              )}
              {!isLoading && current.length === 0 && (
                <TableRow><TableCell colSpan={9} className="py-10 text-center text-muted-foreground">No users match your filters.</TableCell></TableRow>
              )}
              {current.map((u) => (
                <TableRow
                  key={u.id}
                  data-state={selected.has(u.id) ? "selected" : undefined}
                  className="cursor-pointer"
                  onClick={() => navigate(`/users/${u.id}`)}
                >
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Checkbox checked={selected.has(u.id)} onCheckedChange={() => toggleOne(u.id)} />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2 font-medium">
                      {u.username}
                      {u.outbound === "warp" && (
                        <span className="inline-flex items-center gap-1 rounded bg-orange-500/10 px-1.5 py-0.5 text-[10px] font-medium text-orange-400">
                          <Cloud className="h-3 w-3" /> WARP
                        </span>
                      )}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      created {relativeTime(u.created_at)}
                      {isSuperadmin && u.created_by_username && u.created_by_username !== "—" && (
                        <> · by <span className="text-foreground/70">{u.created_by_username}</span></>
                      )}
                      {u.note && <> · {u.note}</>}
                    </div>
                  </TableCell>
                  <TableCell><UserStatusBadge user={u} /></TableCell>
                  <TableCell><QuotaBar used={u.used_bytes} quota={u.quota_bytes} /></TableCell>
                  <TableCell className="whitespace-nowrap text-xs">
                    {formatRate(u.rate_down_kbps)} <span className="opacity-40">/</span> {formatRate(u.rate_up_kbps)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{relativeTime(u.last_seen)}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {u.expires_at ? formatDate(u.expires_at).split(",")[0] : "Never"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{relativeTime(u.created_at)}</TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="h-8 w-8"><MoreHorizontal className="h-4 w-4" /></Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={(e) => { e.stopPropagation(); copyProfile(u); }}>
                          <Copy /> Copy profile
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={(e) => { e.stopPropagation(); copySubLink(u); }}>
                          <Link2 /> Copy sub link
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={(e) => { e.stopPropagation(); setEditing(u); setFormOpen(true); }}>
                          <Pencil /> Edit
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={(e) => { e.stopPropagation(); toggleMut.mutate(u.id); }}>
                          {u.is_active ? <><Ban /> Disable</> : <><CheckCircle2 /> Enable</>}
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={(e) => { e.stopPropagation(); setConfirm({
                          title: `Reset quota for ${u.username}?`,
                          description: "Used traffic returns to 0.",
                          confirmLabel: "Reset",
                          action: () => resetMut.mutate(u.id),
                        }); }}>
                          <RotateCcw /> Reset quota
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className="text-destructive focus:text-destructive"
                          onClick={(e) => { e.stopPropagation(); setConfirm({
                            title: `Delete ${u.username}?`,
                            description: "This cannot be undone.",
                            confirmLabel: "Delete",
                            action: () => deleteMut.mutate(u.id),
                          }); }}
                        >
                          <Trash2 /> Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {pageCount > 1 && (
            <div className="flex items-center justify-between border-t p-3 text-sm">
              <span className="text-muted-foreground">Page {page + 1} of {pageCount}</span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</Button>
                <Button variant="outline" size="sm" disabled={page >= pageCount - 1} onClick={() => setPage((p) => p + 1)}>Next</Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <UserFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        user={editing}
        onSubmit={async (payload) => {
          if (editing) await updateMut.mutateAsync({ id: editing.id, p: payload });
          else await createMut.mutateAsync(payload);
        }}
        saving={createMut.isPending || updateMut.isPending}
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
