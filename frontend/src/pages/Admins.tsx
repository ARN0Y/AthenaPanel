import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, LinkIcon, Plus, Shield, ShieldCheck, Trash2, UserCog } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/widgets/PageHeader";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { api, ApiError, type Admin, type Invite } from "@/lib/api";
import { relativeTime } from "@/lib/format";

function CreateAdminDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [maxUsers, setMaxUsers] = React.useState("0");
  const [canCreate, setCanCreate] = React.useState(true);
  const [note, setNote] = React.useState("");

  React.useEffect(() => {
    if (open) { setUsername(""); setPassword(""); setMaxUsers("0"); setCanCreate(true); setNote(""); }
  }, [open]);

  const mut = useMutation({
    mutationFn: () => api.createAdmin({
      username, password, role: "admin", can_create_users: canCreate,
      max_users: parseInt(maxUsers) || 0, note,
    }),
    onSuccess: () => { toast.success("Admin created"); qc.invalidateQueries({ queryKey: ["admins"] }); onOpenChange(false); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Failed"),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New admin</DialogTitle>
          <DialogDescription>A sub-admin manages only the users they create.</DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => { e.preventDefault(); mut.mutate(); }} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2"><Label>Username</Label><Input value={username} onChange={(e) => setUsername(e.target.value)} required /></div>
            <div className="space-y-2"><Label>Password</Label><Input value={password} onChange={(e) => setPassword(e.target.value)} required /></div>
          </div>
          <div className="space-y-2"><Label>Max users (0 = unlimited)</Label><Input type="number" min="0" value={maxUsers} onChange={(e) => setMaxUsers(e.target.value)} /></div>
          <div className="space-y-2"><Label>Note</Label><Input value={note} onChange={(e) => setNote(e.target.value)} /></div>
          <div className="flex items-center justify-between rounded-md border p-3">
            <Label>Can create users</Label>
            <Switch checked={canCreate} onCheckedChange={setCanCreate} />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={mut.isPending}>Create</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function InviteRow({ inv, onRevoke }: { inv: Invite; onRevoke: (id: number) => void }) {
  const [copied, setCopied] = React.useState(false);
  const link = `${window.location.origin}${import.meta.env.BASE_URL}invite/${inv.token}`;
  const copy = () => { navigator.clipboard.writeText(link); setCopied(true); setTimeout(() => setCopied(false), 1500); };
  const status = inv.used ? <Badge variant="secondary">used</Badge>
    : (inv.expires_at && new Date(inv.expires_at) < new Date()) ? <Badge variant="destructive">expired</Badge>
    : <Badge variant="success">active</Badge>;
  return (
    <TableRow>
      <TableCell>{status}</TableCell>
      <TableCell className="capitalize">{inv.role}</TableCell>
      <TableCell>{inv.max_users === 0 ? "∞" : inv.max_users}</TableCell>
      <TableCell className="text-xs text-muted-foreground">{inv.expires_at ? relativeTime(inv.expires_at) : "never"}</TableCell>
      <TableCell>
        <div className="flex items-center gap-1">
          <Input readOnly value={link} className="h-8 w-[260px] font-mono text-xs" />
          <Button variant="outline" size="icon" className="h-8 w-8" onClick={copy} disabled={inv.used}>
            {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
          </Button>
        </div>
      </TableCell>
      <TableCell className="text-right">
        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => onRevoke(inv.id)}>
          <Trash2 className="h-4 w-4 text-destructive" />
        </Button>
      </TableCell>
    </TableRow>
  );
}

export function Admins() {
  const qc = useQueryClient();
  const { data: admins = [] } = useQuery({ queryKey: ["admins"], queryFn: api.listAdmins, refetchInterval: 20000 });
  const { data: invites = [] } = useQuery({ queryKey: ["invites"], queryFn: api.listInvites, refetchInterval: 20000 });
  const [createOpen, setCreateOpen] = React.useState(false);
  const [confirm, setConfirm] = React.useState<{ title: string; description: string; action: () => void } | null>(null);

  // invite creation form
  const [invMax, setInvMax] = React.useState("0");
  const [invExpiry, setInvExpiry] = React.useState("72");

  const toggleMut = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) => api.updateAdmin(id, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admins"] }),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Failed"),
  });
  const deleteMut = useMutation({
    mutationFn: (id: number) => api.deleteAdmin(id),
    onSuccess: () => { toast.success("Admin deleted (their users were kept)"); qc.invalidateQueries({ queryKey: ["admins"] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Failed"),
  });
  const createInvite = useMutation({
    mutationFn: () => api.createInvite({ role: "admin", max_users: parseInt(invMax) || 0, expires_in_hours: parseInt(invExpiry) || 72 }),
    onSuccess: () => { toast.success("Invite link created"); qc.invalidateQueries({ queryKey: ["invites"] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Failed"),
  });
  const revokeInvite = useMutation({
    mutationFn: (id: number) => api.revokeInvite(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invites"] }),
  });

  return (
    <div>
      <PageHeader title="Admins" description="Operators, permissions and invite links" />
      <Tabs defaultValue="admins">
        <TabsList>
          <TabsTrigger value="admins"><UserCog className="h-4 w-4" /> Admins</TabsTrigger>
          <TabsTrigger value="invites"><LinkIcon className="h-4 w-4" /> Invite links</TabsTrigger>
        </TabsList>

        <TabsContent value="admins">
          <div className="mb-3 flex justify-end">
            <Button size="sm" onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> New admin</Button>
          </div>
          <Card><CardContent className="p-0">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Admin</TableHead><TableHead>Role</TableHead><TableHead>Users</TableHead>
                <TableHead>Status</TableHead><TableHead>Last login</TableHead><TableHead className="text-right">Actions</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {admins.map((a: Admin) => (
                  <TableRow key={a.id}>
                    <TableCell className="font-medium">{a.username}{a.note && <div className="text-xs text-muted-foreground">{a.note}</div>}</TableCell>
                    <TableCell>
                      {a.role === "superadmin"
                        ? <Badge className="gap-1"><ShieldCheck className="h-3 w-3" /> superadmin</Badge>
                        : <Badge variant="secondary" className="gap-1"><Shield className="h-3 w-3" /> admin</Badge>}
                    </TableCell>
                    <TableCell>{a.user_count}{a.max_users > 0 && <span className="text-muted-foreground"> / {a.max_users}</span>}</TableCell>
                    <TableCell>{a.is_active ? <Badge variant="success">active</Badge> : <Badge variant="secondary">disabled</Badge>}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{relativeTime(a.last_login)}</TableCell>
                    <TableCell className="text-right">
                      {a.role !== "superadmin" && (
                        <div className="flex justify-end gap-2">
                          <Switch checked={a.is_active} onCheckedChange={(v) => toggleMut.mutate({ id: a.id, is_active: v })} />
                          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setConfirm({
                            title: `Delete admin ${a.username}?`,
                            description: "Their VPN users are kept and become visible to superadmins. This cannot be undone.",
                            action: () => deleteMut.mutate(a.id),
                          })}>
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent></Card>
        </TabsContent>

        <TabsContent value="invites">
          <Card className="mb-4"><CardContent className="flex flex-wrap items-end gap-3 p-4">
            <div className="space-y-1"><Label className="text-xs">Max users</Label><Input type="number" min="0" className="w-28" value={invMax} onChange={(e) => setInvMax(e.target.value)} /></div>
            <div className="space-y-1"><Label className="text-xs">Expires (hours)</Label>
              <Select value={invExpiry} onValueChange={setInvExpiry}>
                <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="24">24h</SelectItem><SelectItem value="72">3 days</SelectItem>
                  <SelectItem value="168">7 days</SelectItem><SelectItem value="720">30 days</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button size="sm" onClick={() => createInvite.mutate()} disabled={createInvite.isPending}>
              <LinkIcon className="h-4 w-4" /> Generate link
            </Button>
          </CardContent></Card>
          <Card><CardContent className="p-0">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Status</TableHead><TableHead>Role</TableHead><TableHead>Max</TableHead>
                <TableHead>Expires</TableHead><TableHead>Link</TableHead><TableHead className="text-right" />
              </TableRow></TableHeader>
              <TableBody>
                {invites.length === 0 && <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">No invite links</TableCell></TableRow>}
                {invites.map((inv) => <InviteRow key={inv.id} inv={inv} onRevoke={(id) => revokeInvite.mutate(id)} />)}
              </TableBody>
            </Table>
          </CardContent></Card>
        </TabsContent>
      </Tabs>

      <CreateAdminDialog open={createOpen} onOpenChange={setCreateOpen} />
      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={confirm?.title ?? ""}
        description={confirm?.description}
        confirmLabel="Delete"
        onConfirm={() => { confirm?.action(); setConfirm(null); }}
      />
    </div>
  );
}
