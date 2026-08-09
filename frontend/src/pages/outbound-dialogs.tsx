import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Pencil, Plus, Terminal, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import { COUNTRIES, countryName, flagOf } from "@/lib/countries";
import { cn } from "@/lib/utils";

const NAME_RE = /^[a-z0-9][a-z0-9-]{1,11}$/;
const NONE = "__none__";

/** The flag picker. "None" is a real option, not an absence — the operator has
 *  to be able to take a flag off again. */
function CountrySelect({
  value,
  onChange,
  id,
}: {
  value: string;
  onChange: (v: string) => void;
  id?: string;
}) {
  return (
    <Select value={value || NONE} onValueChange={(v) => onChange(v === NONE ? "" : v)}>
      <SelectTrigger id={id}>
        <SelectValue>
          {value ? (
            <span className="flex items-center gap-2">
              <span className="text-base leading-none">{flagOf(value)}</span>
              <span>{countryName(value)}</span>
            </span>
          ) : (
            <span className="text-muted-foreground">No flag</span>
          )}
        </SelectValue>
      </SelectTrigger>
      <SelectContent className="max-h-72">
        <SelectItem value={NONE}>
          <span className="text-muted-foreground">No flag</span>
        </SelectItem>
        {COUNTRIES.map((c) => (
          <SelectItem key={c.code} value={c.code}>
            <span className="flex items-center gap-2">
              <span className="text-base leading-none">{flagOf(c.code)}</span>
              <span>{c.name}</span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      size="icon"
      variant="ghost"
      className="h-7 w-7 shrink-0"
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      aria-label="Copy"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
    </Button>
  );
}

/** Add an egress location.
 *
 * One panel, not a wizard. Everything is on screen from the start: you name it,
 * pick a flag, copy the command, paste back what it printed, and press Add.
 *
 * There IS an unavoidable order underneath — the panel has to allocate the
 * tunnel's addressing and keys before it can print a command, and the command
 * has to run before there is anything to paste back. That is handled by
 * reserving as soon as the name is valid, so the command simply appears in
 * place rather than behind a "next" button. Abandoning the dialog releases the
 * reservation, so a half-finished attempt leaves nothing behind.
 */
export function AddOutboundDialog() {
  const qc = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [country, setCountry] = React.useState("");
  const [note, setNote] = React.useState("");
  const [registration, setRegistration] = React.useState("");
  const [command, setCommand] = React.useState("");
  const [reserved, setReserved] = React.useState<string | null>(null);

  const nameOk = NAME_RE.test(name.trim().toLowerCase()) && !["direct", "warp"].includes(name.trim().toLowerCase());
  const nameTouched = name.length > 0;

  const reset = () => {
    setName(""); setCountry(""); setNote("");
    setRegistration(""); setCommand(""); setReserved(null);
  };

  const reserve = useMutation({
    mutationFn: (n: string) => api.outboundCreate({ name: n, country, note }),
    onSuccess: (r) => { setCommand(r.install_command); setReserved(r.name); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not prepare the command"),
  });

  // Reserve once the name settles, so the command is simply there. Debounced
  // so it does not fire on every keystroke of a name being typed.
  React.useEffect(() => {
    if (!open || !nameOk || reserved) return;
    const n = name.trim().toLowerCase();
    const t = setTimeout(() => reserve.mutate(n), 500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, name, nameOk, reserved]);

  const finish = useMutation({
    mutationFn: () => api.outboundRegister(reserved!, registration.trim()),
    onSuccess: (r) => {
      toast.success(`“${r.name}” is up`, { description: r.endpoint });
      qc.invalidateQueries({ queryKey: ["outbounds"] });
      setOpen(false);
      reset();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Registration failed"),
  });

  // Closing without finishing must not leave a reservation holding a name, a
  // mark and a /30 that nothing will ever use.
  const close = (o: boolean) => {
    if (!o && reserved && !finish.isSuccess) {
      api.outboundDelete(reserved).catch(() => {});
      qc.invalidateQueries({ queryKey: ["outbounds"] });
    }
    setOpen(o);
    if (!o) reset();
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="shrink-0">
          <Plus className="h-4 w-4" /> Add outbound
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add an egress location</DialogTitle>
          <DialogDescription>
            A WireGuard tunnel to a server you own. Traffic from the users you assign to it
            leaves the internet from that server's address.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-[1fr_220px]">
            <div className="space-y-1.5">
              <Label htmlFor="ob-name">Name</Label>
              <div className="relative">
                <Input
                  id="ob-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="de-fra"
                  autoFocus
                  autoComplete="off"
                  className={cn("pr-8", nameTouched && !nameOk && "border-destructive")}
                  disabled={!!reserved}
                />
                {name && (
                  <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-base leading-none">
                    {flagOf(country)}
                  </span>
                )}
              </div>
              <p className={cn("text-[11px]", nameTouched && !nameOk ? "text-destructive" : "text-muted-foreground")}>
                2–12 characters of a–z, 0–9 or “-”. This is what you'll see everywhere, and it
                names the tunnel interface.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ob-country">Flag</Label>
              <CountrySelect id="ob-country" value={country} onChange={setCountry} />
              <p className="text-[11px] text-muted-foreground">Optional.</p>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ob-note">Note</Label>
            <Input
              id="ob-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Optional — what this server is for"
              disabled={!!reserved}
            />
          </div>

          <div className="space-y-1.5">
            <Label className="flex items-center gap-1.5">
              <Terminal className="h-3.5 w-3.5" /> Run this on the egress server
            </Label>
            <div
              className={cn(
                "flex items-start gap-2 rounded-md border bg-muted/40 p-3 transition-opacity",
                !command && "opacity-60",
              )}
            >
              <code className="flex-1 break-all font-mono text-[11px] leading-relaxed">
                {command || (
                  <span className="text-muted-foreground">
                    {reserve.isPending
                      ? "Preparing…"
                      : "Enter a name above and the command appears here."}
                  </span>
                )}
              </code>
              {command && <CopyButton text={command} />}
            </div>
            <p className="text-[11px] text-muted-foreground">
              Ubuntu or Debian, x86 or ARM. The command contains a pre-shared key — treat it
              like a password.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ob-reg">Then paste the line it printed</Label>
            <Input
              id="ob-reg"
              value={registration}
              onChange={(e) => setRegistration(e.target.value)}
              placeholder="athena-ob:203.0.113.9:51833:…"
              className="font-mono text-xs"
              disabled={!command}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => close(false)}>Cancel</Button>
          <Button
            disabled={!command || !registration.trim() || finish.isPending}
            onClick={() => finish.mutate()}
          >
            {finish.isPending ? "Bringing the tunnel up…" : "Add outbound"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Rename an outbound and/or change its flag. */
export function EditOutboundDialog({ current, country: initialCountry }: { current: string; country: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState(current);
  const [country, setCountry] = React.useState(initialCountry || "");

  React.useEffect(() => {
    if (open) { setName(current); setCountry(initialCountry || ""); }
  }, [open, current, initialCountry]);

  const nameOk = NAME_RE.test(name.trim().toLowerCase()) && !["direct", "warp"].includes(name.trim().toLowerCase());
  const renaming = name.trim().toLowerCase() !== current;

  const save = useMutation({
    mutationFn: () => api.outboundUpdate(current, { name: name.trim().toLowerCase(), country }),
    onSuccess: (r) => {
      toast.success(r.renamed ? `Renamed to “${r.name}”` : "Saved");
      qc.invalidateQueries({ queryKey: ["outbounds"] });
      qc.invalidateQueries({ queryKey: ["users"] });
      setOpen(false);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not save"),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          size="icon"
          variant="ghost"
          className="h-6 w-6 text-muted-foreground hover:text-foreground"
          aria-label={`Edit ${current}`}
        >
          <Pencil className="h-3.5 w-3.5" />
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit “{current}”</DialogTitle>
          <DialogDescription>The server itself is not touched.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="ob-edit-name">Name</Label>
            <Input
              id="ob-edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="off"
              className={cn(!nameOk && "border-destructive")}
            />
            {!nameOk && (
              <p className="text-[11px] text-destructive">
                2–12 characters of a–z, 0–9 or “-”.
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ob-edit-country">Flag</Label>
            <CountrySelect id="ob-edit-country" value={country} onChange={setCountry} />
          </div>
          {renaming && nameOk && (
            <p className="rounded-md bg-muted/50 px-3 py-2 text-[11px] text-muted-foreground">
              Renaming rebuilds the tunnel, so its users egress directly for a second or two.
              Users assigned to it move across with it.
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button disabled={!nameOk || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function DeleteOutboundButton({ name, users }: { name: string; users: number }) {
  const qc = useQueryClient();
  const del = useMutation({
    mutationFn: () => api.outboundDelete(name),
    onSuccess: (r) => {
      toast.success(`Removed “${name}”`, {
        description: r.moved_to_direct
          ? `${r.moved_to_direct} user(s) moved to Direct`
          : "No users were assigned to it",
      });
      qc.invalidateQueries({ queryKey: ["outbounds"] });
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not remove it"),
  });

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button
          size="icon"
          variant="ghost"
          className="h-6 w-6 text-muted-foreground hover:text-destructive"
          aria-label={`Remove ${name}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove “{name}”?</AlertDialogTitle>
          <AlertDialogDescription>
            The tunnel is torn down here and{" "}
            {users > 0
              ? `${users} user(s) assigned to it move back to Direct.`
              : "no users are assigned to it."}{" "}
            The egress server itself keeps running — nothing is changed there.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={() => del.mutate()}>Remove</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
