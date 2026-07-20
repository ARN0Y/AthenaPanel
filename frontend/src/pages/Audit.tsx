import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Ban,
  CheckCircle2,
  Database,
  KeyRound,
  LogIn,
  Mail,
  PlusCircle,
  Power,
  RotateCcw,
  Search,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  ShieldPlus,
  Trash2,
  UserCog,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/widgets/PageHeader";
import { api } from "@/lib/api";
import { formatDate, relativeTime } from "@/lib/format";

const ACTION_META: Record<string, { icon: React.ComponentType<{ className?: string }>; label: string; variant: "default" | "secondary" | "destructive" | "success" | "warning" | "outline" }> = {
  create_user: { icon: PlusCircle, label: "Create", variant: "success" },
  update_user: { icon: UserCog, label: "Update", variant: "default" },
  delete_user: { icon: Trash2, label: "Delete", variant: "destructive" },
  enable_user: { icon: CheckCircle2, label: "Enable", variant: "success" },
  disable_user: { icon: Ban, label: "Disable", variant: "secondary" },
  reset_quota: { icon: RotateCcw, label: "Reset quota", variant: "warning" },
  disconnect: { icon: Power, label: "Disconnect", variant: "warning" },
  // Operator-level and security events — previously all fell through to the
  // generic grey pill, which buried the ones that matter most.
  login: { icon: LogIn, label: "Login", variant: "outline" },
  login_failed: { icon: ShieldAlert, label: "Login failed", variant: "destructive" },
  change_password: { icon: KeyRound, label: "Password change", variant: "warning" },
  create_admin: { icon: ShieldPlus, label: "Create admin", variant: "warning" },
  update_admin: { icon: ShieldCheck, label: "Update admin", variant: "warning" },
  delete_admin: { icon: ShieldAlert, label: "Delete admin", variant: "destructive" },
  create_invite: { icon: Mail, label: "Create invite", variant: "warning" },
  invite_accept: { icon: ShieldPlus, label: "Invite accepted", variant: "warning" },
  update_settings: { icon: Settings2, label: "Settings", variant: "warning" },
  reject_session: { icon: ShieldAlert, label: "Session refused", variant: "destructive" },
  wg_enable: { icon: CheckCircle2, label: "WireGuard on", variant: "success" },
  wg_disable: { icon: Ban, label: "WireGuard off", variant: "secondary" },
  backup_create: { icon: Database, label: "Backup", variant: "outline" },
  backup_delete: { icon: Trash2, label: "Backup deleted", variant: "destructive" },
  backup_telegram: { icon: Database, label: "Backup sent", variant: "outline" },
};

function meta(action: string) {
  if (ACTION_META[action]) return ACTION_META[action];
  if (action.startsWith("bulk_")) return { icon: Settings2, label: action.replace("bulk_", "bulk "), variant: "outline" as const };
  return { icon: Settings2, label: action, variant: "outline" as const };
}

export function Audit() {
  const { data: entries = [], isLoading } = useQuery({
    queryKey: ["audit"],
    queryFn: () => api.audit(500),
    refetchInterval: 15000,
  });
  const [search, setSearch] = React.useState("");
  const [actor, setActor] = React.useState("all");

  const actors = React.useMemo(
    () => Array.from(new Set(entries.map((e) => e.actor).filter(Boolean))).sort(),
    [entries],
  );

  const filtered = React.useMemo(() => {
    let list = entries;
    if (actor !== "all") list = list.filter((e) => e.actor === actor);
    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter((e) =>
        [e.target, e.actor, e.action, e.detail, meta(e.action).label]
          .some((s) => (s || "").toLowerCase().includes(q)),
      );
    }
    return list;
  }, [entries, actor, search]);

  return (
    <div>
      <PageHeader title="Audit log" description="Every administrative action taken in the panel" />
      <Card>
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center gap-3 border-b p-4">
            <div className="relative min-w-[200px] flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input placeholder="Search action, user or actor…" className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            <Select value={actor} onValueChange={setActor}>
              <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any operator</SelectItem>
                {actors.map((a) => (
                  <SelectItem key={a} value={a}>{a}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Badge variant="outline" className="ml-auto">{filtered.length} entries</Badge>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Target</TableHead>
                <TableHead>Details</TableHead>
                <TableHead>Actor</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading && (
                <TableRow><TableCell colSpan={5} className="py-10 text-center text-muted-foreground">Loading…</TableCell></TableRow>
              )}
              {!isLoading && filtered.length === 0 && (
                <TableRow><TableCell colSpan={5} className="py-10 text-center text-muted-foreground">No actions match.</TableCell></TableRow>
              )}
              {filtered.map((e) => {
                const m = meta(e.action);
                const Icon = m.icon;
                return (
                  <TableRow key={e.id}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground" title={formatDate(e.ts)}>
                      {relativeTime(e.ts)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={m.variant} className="gap-1.5">
                        <Icon className="h-3 w-3" /> {m.label}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-medium">{e.target || "—"}</TableCell>
                    <TableCell className="max-w-[460px] whitespace-normal break-words text-xs text-muted-foreground" title={e.detail || ""}>{e.detail || "—"}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{e.actor}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
