import * as React from "react";

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
    } else {
      setUsername("");
      setPassword("");
      setQuotaGb("0");
      setDownMbps("0");
      setUpMbps("0");
      setExpiry("");
      setNote("");
      setIsActive(true);
      setOutbound("direct");
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
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit User" : "Create User"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update limits, expiry and status. Leave password blank to keep it."
              : "Set 0 for unlimited quota or speed."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="u-username">Username</Label>
              <Input
                id="u-username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={isEdit}
                required={!isEdit}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="u-password">Password{isEdit ? " (new)" : ""}</Label>
              <Input
                id="u-password"
                type="text"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isEdit ? "unchanged" : ""}
                required={!isEdit}
              />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-2">
              <Label htmlFor="u-quota">Quota (GB)</Label>
              <Input
                id="u-quota"
                type="number"
                min="0"
                step="0.1"
                value={quotaGb}
                onChange={(e) => setQuotaGb(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="u-down">Down (Mbps)</Label>
              <Input
                id="u-down"
                type="number"
                min="0"
                step="0.5"
                value={downMbps}
                onChange={(e) => setDownMbps(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="u-up">Up (Mbps)</Label>
              <Input
                id="u-up"
                type="number"
                min="0"
                step="0.5"
                value={upMbps}
                onChange={(e) => setUpMbps(e.target.value)}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="u-expiry">Expiry date</Label>
              <Input
                id="u-expiry"
                type="date"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="u-outbound">Outbound</Label>
              <Select value={outbound} onValueChange={setOutbound}>
                <SelectTrigger id="u-outbound">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="direct">Direct</SelectItem>
                  <SelectItem value="warp">Cloudflare WARP</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="u-note">Note</Label>
            <Input id="u-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <Label htmlFor="u-active">Active</Label>
              <p className="text-xs text-muted-foreground">
                Disabled users cannot connect.
              </p>
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
