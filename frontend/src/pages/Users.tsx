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
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Search,
  Server,
  Trash2,
  UserCog,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
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
import { cn } from "@/lib/utils";

type SortKey = "created_at" | "username" | "used_bytes" | "last_seen" | "expires_at" | "rate_down_kbps";
type StatusFilter = "all" | "online" | "offline" | "disabled" | "expired";
// Twelve was an arbitrary number that made an operator with a hundred accounts
// page constantly. Twenty fills a laptop screen; the rest is their choice.
const PAGE_SIZES = [20, 50, 100] as const;

/** A stable colour per account, derived from the name.
 *
 *  Every username here looks like "Mgh10xx", so a list of them is a wall of
 *  near-identical strings. A colour the operator's eye can latch onto turns
 *  "find that row again" from reading into recognising — and deriving it from
 *  the name means it never changes, which is the only reason it works. */
const AVATAR_TONES = [
  "bg-sky-500/15 text-sky-300",
  "bg-violet-500/15 text-violet-300",
  "bg-emerald-500/15 text-emerald-300",
  "bg-amber-500/15 text-amber-300",
  "bg-rose-500/15 text-rose-300",
  "bg-cyan-500/15 text-cyan-300",
  "bg-fuchsia-500/15 text-fuchsia-300",
  "bg-lime-500/15 text-lime-300",
];

function Identicon({ name, online }: { name: string; online: boolean }) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  const tone = AVATAR_TONES[hash % AVATAR_TONES.length];
  // First character + last, not the first two. Real accounts here are named
  // "Mgh1074", "Mgh1073" — identical until the end — while a name like "yasmin"
  // is identical to "nazanin" at the end. Taking one from each end tells both
  // kinds apart; either alone collides on one of them.
  const clean = name.replace(/[^A-Za-z0-9]/g, "");
  const label = (clean.length > 1 ? clean[0] + clean[clean.length - 1] : clean || "?").toUpperCase();
  return (
    <span className="relative shrink-0">
      <span
        className={cn(
          "flex h-8 w-8 items-center justify-center rounded-lg text-[11px] font-semibold tabular-nums",
          tone,
        )}
      >
        {label}
      </span>
      {online && (
        <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-card bg-success" />
      )}
    </span>
  );
}

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
  // Superadmin-only: "which operator provisioned this account?" — the answer is
  // already on every row, this makes it filterable when there are hundreds.
  const [creator, setCreator] = React.useState("all");
  // WARP membership is otherwise only visible as a per-row badge, so "who is on
  // WARP right now?" meant scrolling every page.
  const [outbound, setOutbound] = React.useState("all");
  const [sortKey, setSortKey] = React.useState<SortKey>("created_at");
  const [sortDir, setSortDir] = React.useState<"asc" | "desc">("desc");
  const [page, setPage] = React.useState(0);
  const [pageSize, setPageSize] = React.useState<number>(PAGE_SIZES[0]);
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
    mutationFn: ({ ids, action, ownerAdminId }: { ids: number[]; action: BulkActionType; ownerAdminId?: number }) =>
      api.bulk(ids, action, ownerAdminId),
    onSuccess: (res) => { toast.success(`${res.action}: ${res.affected.length} user(s)`); setSelected(new Set()); invalidate(); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Bulk action failed"),
  });

  // Candidate owners for the bulk hand-over. Superadmin-only in both
  // directions: the endpoint refuses a reseller and the control is hidden
  // from one.
  const { data: admins = [] } = useQuery({
    queryKey: ["admins"],
    queryFn: api.listAdmins,
    enabled: isSuperadmin,
    retry: false,
  });

  const creators = React.useMemo(
    () => Array.from(new Set(users.map((u) => u.created_by_username).filter((n) => n && n !== "—"))).sort(),
    [users],
  );
  const warpCount = React.useMemo(() => users.filter((u) => u.outbound === "warp").length, [users]);

  const filtered = React.useMemo(() => {
    let list = users.filter((u) => matchesStatus(u, status));
    if (creator !== "all") list = list.filter((u) => (u.created_by_username || "—") === creator);
    if (outbound !== "all") list = list.filter((u) => (u.outbound || "direct") === outbound);
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
  }, [users, status, creator, outbound, search, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const current = filtered.slice(page * pageSize, page * pageSize + pageSize);
  // The row number is the position in the WHOLE filtered set, not on this page.
  // A number that restarts at 1 every page is decoration; one that does not is
  // something an operator can actually refer to.
  const firstRowNumber = page * pageSize + 1;

  // `creator` and `outbound` belong here too: changing a filter while on page 3
  // used to leave you on page 3 of a now-shorter list, i.e. staring at an empty
  // table with no hint why.
  React.useEffect(() => { setPage(0); }, [search, status, creator, outbound, sortKey, sortDir]);

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
    // Quote properly rather than stripping commas: a note containing a quote or
    // a newline used to shift every following column in the exported file.
    const cell = (v: string | number) => {
      const s = String(v ?? "");
      return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const rows = [
      ["username", "status", "outbound", "used_bytes", "quota_bytes", "down_kbps", "up_kbps", "expires_at", "last_seen", "note"],
      ...filtered.map((u) => [
        u.username,
        u.is_active ? (u.is_expired ? "expired" : "active") : "disabled",
        u.outbound || "direct",
        u.used_bytes, u.quota_bytes, u.rate_down_kbps, u.rate_up_kbps,
        u.expires_at ?? "", u.last_seen ?? "", u.note,
      ]),
    ];
    // BOM so Excel opens UTF-8 usernames/notes correctly.
    const csv = "﻿" + rows.map((r) => r.map(cell).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `vpn-users-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
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
            {warpCount > 0 && (
              <Select value={outbound} onValueChange={setOutbound}>
                <SelectTrigger className="w-36">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any outbound</SelectItem>
                  <SelectItem value="warp">WARP ({warpCount})</SelectItem>
                  <SelectItem value="direct">Direct</SelectItem>
                </SelectContent>
              </Select>
            )}
            {isSuperadmin && creators.length > 0 && (
              <Select value={creator} onValueChange={setCreator}>
                <SelectTrigger className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any creator</SelectItem>
                  <SelectItem value="—">Unassigned</SelectItem>
                  {creators.map((n) => (
                    <SelectItem key={n} value={n}>Created by {n}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
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
                {isSuperadmin && admins.length > 1 && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" size="sm">
                        <UserCog className="h-4 w-4" /> Assign to
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="max-h-72 overflow-y-auto">
                      {admins
                        .filter((a) => a.is_active)
                        .map((a) => (
                          <DropdownMenuItem
                            key={a.id}
                            onClick={() =>
                              setConfirm({
                                title: `Hand ${selected.size} account(s) to ${a.username}?`,
                                description:
                                  a.role === "superadmin"
                                    ? "They will manage these accounts from now on. Nothing is disconnected."
                                    : `${a.username} currently holds ${a.user_count}` +
                                      (a.max_users > 0 ? ` of ${a.max_users}` : "") +
                                      ". Nothing is disconnected.",
                                confirmLabel: "Assign",
                                action: () =>
                                  bulkMut.mutate({ ids, action: "assign", ownerAdminId: a.id }),
                              })
                            }
                          >
                            {a.username}
                            <span className="ml-auto pl-3 text-xs text-muted-foreground">
                              {a.role === "superadmin"
                                ? "superadmin"
                                : a.max_users > 0
                                  ? `${a.user_count}/${a.max_users}`
                                  : a.user_count}
                            </span>
                          </DropdownMenuItem>
                        ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
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

          <div className="overflow-x-auto">
          <Table>
            {/* Sticky, because twenty rows means the operator is scrolling and a
                column they cannot see is a column they have to guess at. */}
            <TableHeader className="sticky top-0 z-10 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[52px] pl-4">
                  <div className="flex items-center">
                    <Checkbox
                      checked={allChecked}
                      onCheckedChange={toggleAll}
                      aria-label="Select every user on this page"
                    />
                  </div>
                </TableHead>
                <SortHead k="username">User</SortHead>
                <TableHead>Status</TableHead>
                <SortHead k="used_bytes" className="min-w-[190px]">Quota</SortHead>
                <SortHead k="rate_down_kbps" className="text-right">Speed</SortHead>
                <SortHead k="last_seen" className="text-right">Last seen</SortHead>
                <SortHead k="expires_at" className="text-right">Expiry</SortHead>
                <SortHead k="created_at" className="text-right">Created</SortHead>
                <TableHead className="w-12 pr-4" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading &&
                Array.from({ length: 6 }).map((_, i) => (
                  <TableRow key={`sk-${i}`} className="hover:bg-transparent">
                    <TableCell colSpan={9} className="py-3">
                      <Skeleton className="h-9 w-full" />
                    </TableCell>
                  </TableRow>
                ))}
              {!isLoading && current.length === 0 && (
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={9} className="py-16 text-center">
                    <div className="mx-auto flex max-w-sm flex-col items-center gap-2">
                      <div className="flex h-11 w-11 items-center justify-center rounded-full bg-muted">
                        <Search className="h-5 w-5 text-muted-foreground" />
                      </div>
                      <p className="text-sm font-medium">No users match your filters</p>
                      <p className="text-xs text-muted-foreground">
                        Try clearing the search or widening the status filter.
                      </p>
                    </div>
                  </TableCell>
                </TableRow>
              )}
              {current.map((u, i) => (
                <TableRow
                  key={u.id}
                  data-state={selected.has(u.id) ? "selected" : undefined}
                  className={cn(
                    "group cursor-pointer border-l-2 transition-colors",
                    // Conditional, not a data-[state] variant stacked on a base
                    // colour: two border-l-* utilities on one element leave the
                    // winner up to stylesheet order, and the accent silently
                    // never appeared.
                    selected.has(u.id) ? "border-l-primary" : "border-l-transparent",
                  )}
                  onClick={() => navigate(`/users/${u.id}`)}
                >
                  {/* Number by default, checkbox on hover or once selected. The
                      operator gets a row they can refer to AND selection, in
                      one column instead of two. */}
                  <TableCell className="pl-4" onClick={(e) => e.stopPropagation()}>
                    <div className="relative flex h-5 w-5 items-center justify-center">
                      {/* Exactly one opacity utility per state. Giving an element
                          both opacity-0 and opacity-100 and letting a variant
                          decide leaves the winner to stylesheet order — which is
                          how the checkbox ended up invisible on a selected row. */}
                      <span
                        className={cn(
                          "absolute text-xs tabular-nums text-muted-foreground/60 transition-opacity",
                          selected.has(u.id) ? "opacity-0" : "opacity-100 group-hover:opacity-0",
                        )}
                        aria-hidden
                      >
                        {firstRowNumber + i}
                      </span>
                      <Checkbox
                        checked={selected.has(u.id)}
                        onCheckedChange={() => toggleOne(u.id)}
                        aria-label={`Select ${u.username}`}
                        className={cn(
                          "absolute transition-opacity",
                          selected.has(u.id) ? "opacity-100" : "opacity-0 group-hover:opacity-100",
                        )}
                      />
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2.5">
                      <Identicon name={u.username} online={u.online} />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate font-medium">{u.username}</span>
                          {u.outbound === "warp" && (
                            <span
                              title="Egress via WARP"
                              className="inline-flex items-center gap-0.5 rounded bg-orange-500/10 px-1 py-0.5 text-[10px] font-medium text-orange-400"
                            >
                              <Cloud className="h-2.5 w-2.5" /> WARP
                            </span>
                          )}
                          {/* Only worth the pixels once more than one node exists. */}
                          {isSuperadmin && u.node_id !== 1 && (
                            <span
                              className="inline-flex items-center gap-0.5 rounded bg-primary/10 px-1 py-0.5 text-[10px] font-medium text-primary"
                              title={`Terminated by node ${u.node_name}`}
                            >
                              <Server className="h-2.5 w-2.5" /> {u.node_name}
                            </span>
                          )}
                        </div>
                        {/* Only rendered when there is something to say. A line
                            that reads "no reseller" on every row is decoration. */}
                        {(u.note || (isSuperadmin && u.created_by_username && u.created_by_username !== "—")) && (
                          <div className="truncate text-[11px] text-muted-foreground">
                            {isSuperadmin && u.created_by_username && u.created_by_username !== "—" && (
                              <span className="text-foreground/60">{u.created_by_username}</span>
                            )}
                            {u.note && (
                              <>
                                {isSuperadmin && u.created_by_username && u.created_by_username !== "—" && " · "}
                                {u.note}
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell><UserStatusBadge user={u} /></TableCell>
                  <TableCell><QuotaBar used={u.used_bytes} quota={u.quota_bytes} /></TableCell>
                  <TableCell className="whitespace-nowrap text-right font-mono text-xs tabular-nums">
                    <span className="text-foreground/80">{formatRate(u.rate_down_kbps)}</span>
                    <span className="mx-1 opacity-30">/</span>
                    <span className="text-muted-foreground">{formatRate(u.rate_up_kbps)}</span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right text-xs text-muted-foreground">
                    {relativeTime(u.last_seen)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right text-xs">
                    {u.expires_at ? (
                      <span className={cn(u.is_expired && "font-medium text-destructive")}>
                        {formatDate(u.expires_at).split(",")[0]}
                      </span>
                    ) : (
                      <span className="text-muted-foreground/60">Never</span>
                    )}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right text-xs text-muted-foreground">
                    {relativeTime(u.created_at)}
                  </TableCell>
                  <TableCell className="pr-4" onClick={(e) => e.stopPropagation()}>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100"
                        >
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
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

          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-sm">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="tabular-nums">
                {filtered.length === 0
                  ? "No users"
                  : `${firstRowNumber}–${Math.min(firstRowNumber + current.length - 1, filtered.length)} of ${filtered.length}`}
              </span>
              {selected.size > 0 && (
                <span className="text-foreground">· {selected.size} selected</span>
              )}
            </div>

            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Rows</span>
                <Select
                  value={String(pageSize)}
                  onValueChange={(v) => { setPageSize(Number(v)); setPage(0); }}
                >
                  <SelectTrigger className="h-8 w-[72px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PAGE_SIZES.map((n) => (
                      <SelectItem key={n} value={String(n)}>{n}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex items-center gap-1">
                <span className="mr-1 text-xs tabular-nums text-muted-foreground">
                  Page {page + 1} of {pageCount}
                </span>
                <Button variant="outline" size="icon" className="h-8 w-8"
                        disabled={page === 0} onClick={() => setPage(0)} title="First page">
                  <ChevronsLeft className="h-4 w-4" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8"
                        disabled={page === 0} onClick={() => setPage((p) => p - 1)} title="Previous page">
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8"
                        disabled={page >= pageCount - 1} onClick={() => setPage((p) => p + 1)} title="Next page">
                  <ChevronRight className="h-4 w-4" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8"
                        disabled={page >= pageCount - 1} onClick={() => setPage(pageCount - 1)} title="Last page">
                  <ChevronsRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </div>
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
