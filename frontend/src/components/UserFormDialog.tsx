import * as React from "react";
import { Gauge, KeyRound, RefreshCw, ShieldCheck, ShieldOff, UserRound } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { User, UserPayload } from "@/lib/api";
import { bytesToGb, gbToBytes, kbpsToMbps, mbpsToKbps } from "@/lib/format";

interface UserFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user?: User | null;
  onSubmit: (payload: UserPayload) => Promise<void>;
  saving: boolean;
}

function toDateInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().slice(0, 10);
}

function randomPassword(len = 12): string {
  const chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let out = "";
  const arr = new Uint32Array(len);
  crypto.getRandomValues(arr);
  for (let i = 0; i < len; i++) out += chars[arr[i] % chars.length];
  return out;
}

function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <Label htmlFor={htmlFor} className="text-xs text-muted-foreground">
          {label}
        </Label>
        {hint && <span className="text-[10px] text-muted-foreground/60">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function SectionTitle({
  icon: Icon,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70">
      <Icon className="h-3.5 w-3.5" />
      {children}
    </div>
  );
}

export function UserFormDialog({
  open,
  onOpenChange,
  user,
  onSubmit,
  saving,
}: UserFormDialogProps) {
  const isEdit = !!user;
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [quotaGb, setQuotaGb] = React.useState("0");
  const [downMbps, setDownMbps] = React.useState("0");
  const [upMbps, setUpMbps] = React.useState("0");
  const [expiry, setExpiry] = React.useState("");
  const [note, setNote] = React.useState("");
  const [isActive, setIsActive] = React.useState(true);
  const [outbound, setOutbound] = React.useState("direct");
  const [l2tpMode, setL2tpMode] = React.useState("ipsec");

  React.useEffect(() => {
    if (!open) return;
    if (user) {
      setUsername(user.username);
      setPassword("");
      setQuotaGb(String(+bytesToGb(user.quota_bytes).toFixed(2)));
      setDownMbps(String(kbpsToMbps(user.rate_down_kbps)));
      setUpMbps(String(kbpsToMbps(user.rate_up_kbps)));
      setExpiry(toDateInput(user.expires_at));
      setNote(user.note);
      setIsActive(user.is_active);
      setOutbound(user.outbound || "direct");
      setL2tpMode(user.l2tp_mode || "ipsec");
    } else {
      setUsername("");
      setPassword(randomPassword());
      setQuotaGb("0");
      setDownMbps("0");
      setUpMbps("0");
      setExpiry("");
      setNote("");
      setIsActive(true);
      setOutbound("direct");
      setL2tpMode("ipsec");
    }
  }, [open, user]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload: UserPayload = {
      quota_bytes: gbToBytes(parseFloat(quotaGb) || 0),
      rate_down_kbps: mbpsToKbps(parseFloat(downMbps) || 0),
      rate_up_kbps: mbpsToKbps(parseFloat(upMbps) || 0),
      is_active: isActive,
      expires_at: expiry ? new Date(expiry + "T23:59:59Z").toISOString() : null,
      note,
      outbound,
      l2tp_mode: l2tpMode,
    };
    if (!isEdit) {
      payload.username = username;
      payload.password = password;
    } else if (password) {
      payload.password = password;
    }
    await onSubmit(payload);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-xl bg-primary/12 ring-1 ring-primary/20">
            <UserRound className="h-5 w-5 text-primary" />
          </div>
          <DialogTitle className="text-lg">{isEdit ? `Edit ${user?.username}` : "Create user"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update limits, access and status. Leave password blank to keep it."
              : "A new VPN account usable on L2TP, SSTP and WireGuard."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          <div className="space-y-3">
            <SectionTitle icon={KeyRound}>Account</SectionTitle>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Username" htmlFor="u-username">
                <Input
                  id="u-username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={isEdit}
                  required={!isEdit}
                  autoComplete="off"
                />
              </Field>
              <Field label={isEdit ? "Password (new)" : "Password"} htmlFor="u-password">
                <div className="flex gap-1.5">
                  <Input
                    id="u-password"
                    type="text"
                    className="font-mono"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={isEdit ? "unchanged" : ""}
                    required={!isEdit}
                    autoComplete="off"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="shrink-0"
                    title="Generate password"
                    onClick={() => setPassword(randomPassword())}
                  >
                    <RefreshCw className="h-4 w-4" />
                  </Button>
                </div>
              </Field>
            </div>
          </div>

          <div className="space-y-3">
            <SectionTitle icon={Gauge}>Limits</SectionTitle>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Quota" hint="GB · 0 = ∞" htmlFor="u-quota">
                <Input id="u-quota" type="number" min="0" step="0.1" value={quotaGb} onChange={(e) => setQuotaGb(e.target.value)} />
              </Field>
              <Field label="Download" hint="Mbps · 0 = ∞" htmlFor="u-down">
                <Input id="u-down" type="number" min="0" step="0.5" value={downMbps} onChange={(e) => setDownMbps(e.target.value)} />
              </Field>
              <Field label="Upload" hint="Mbps · 0 = ∞" htmlFor="u-up">
                <Input id="u-up" type="number" min="0" step="0.5" value={upMbps} onChange={(e) => setUpMbps(e.target.value)} />
              </Field>
            </div>
          </div>

          <div className="space-y-3">
            <SectionTitle icon={ShieldCheck}>Access</SectionTitle>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Expiry date" hint="empty = never" htmlFor="u-expiry">
                <Input id="u-expiry" type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
              </Field>
              <Field label="Outbound" htmlFor="u-outbound">
                <Select value={outbound} onValueChange={setOutbound}>
                  <SelectTrigger id="u-outbound">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="direct">Direct</SelectItem>
                    <SelectItem value="warp">Cloudflare WARP</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </div>
            <Field label="L2TP mode" hint="how the client connects" htmlFor="u-l2tp-mode">
              <Select value={l2tpMode} onValueChange={setL2tpMode}>
                <SelectTrigger id="u-l2tp-mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ipsec">L2TP / IPsec — encrypted (recommended)</SelectItem>
                  <SelectItem value="raw">L2TP raw — without IPsec</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            {l2tpMode === "raw" && (
              <div className="flex gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-200/90">
                <ShieldOff className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
                <p>
                  Raw mode carries traffic <strong>and credentials unencrypted</strong> — only use it when the
                  customer's ISP blocks IKE/ESP. It connects to a <strong>different server address</strong>,
                  shown on the user's page after saving.
                </p>
              </div>
            )}

            <Field label="Note" htmlFor="u-note">
              <Input id="u-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional label…" />
            </Field>
          </div>

          <div className="flex items-center justify-between rounded-lg border bg-muted/30 p-3">
            <div>
              <Label htmlFor="u-active" className="text-sm font-medium">
                Account active
              </Label>
              <p className="text-xs text-muted-foreground">Disabled accounts cannot connect.</p>
            </div>
            <Switch id="u-active" checked={isActive} onCheckedChange={setIsActive} />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {isEdit ? "Save changes" : "Create user"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
