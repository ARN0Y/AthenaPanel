import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Image as ImageIcon, Loader2, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ApiError,
  api,
  brandingThumbUrl,
  type Branding,
  type BrandingPayload,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Sign-in screen appearance.
 *
 * Every control edits a local DRAFT and the preview renders from that draft, so
 * dragging the dim slider is one continuous adjustment rather than forty saves
 * and forty toasts. Nothing reaches the server until Save. Uploading is the one
 * exception — a file has to be sent to exist at all — and it is explicit enough
 * that a single confirmation is right.
 *
 * Values are stored in `app_settings`; artwork is a file library. See
 * backend/app/branding.py.
 */

const LAYOUTS: { id: Branding["login_layout"]; label: string; hint: string }[] = [
  { id: "split-right", label: "Split · right", hint: "Form left, artwork right" },
  { id: "split-left", label: "Split · left", hint: "Artwork left, form right" },
  { id: "centered", label: "Centered", hint: "Card floating on the artwork" },
  { id: "backdrop", label: "Backdrop", hint: "Same, with a heavier veil" },
];

const FOCALS: Branding["login_focal"][] = ["center", "top", "bottom", "left", "right"];

function kb(n: number) {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`;
}

/** A small drawing of each arrangement — quicker to read than the words. */
function LayoutThumb({ id, active }: { id: Branding["login_layout"]; active: boolean }) {
  const art = (
    <div className={cn("rounded-[2px]", active ? "bg-primary/45" : "bg-muted-foreground/25")} />
  );
  const form = (
    <div className="flex flex-col justify-center gap-[3px] px-1.5">
      <div
        className={cn(
          "h-[3px] w-2/3 rounded-full",
          active ? "bg-primary/70" : "bg-muted-foreground/45",
        )}
      />
      <div className="h-[2px] w-full rounded-full bg-muted-foreground/25" />
      <div className="h-[2px] w-full rounded-full bg-muted-foreground/25" />
    </div>
  );

  if (id === "centered" || id === "backdrop") {
    return (
      <div className="relative h-12 w-full overflow-hidden rounded-[3px] border border-border/60">
        <div className={cn("absolute inset-0", active ? "bg-primary/30" : "bg-muted-foreground/20")} />
        {id === "backdrop" && <div className="absolute inset-0 bg-background/50" />}
        <div className="absolute inset-x-[28%] inset-y-[18%] rounded-[2px] border border-border/70 bg-background/85" />
      </div>
    );
  }
  return (
    <div className="grid h-12 w-full grid-cols-2 overflow-hidden rounded-[3px] border border-border/60">
      {id === "split-right" ? (
        <>
          {form}
          {art}
        </>
      ) : (
        <>
          {art}
          {form}
        </>
      )}
    </div>
  );
}

type Draft = Pick<
  Branding,
  | "brand_name"
  | "login_tagline"
  | "login_layout"
  | "login_focal"
  | "login_overlay"
  | "login_image_url"
  | "login_image_id"
>;

const toDraft = (b: Branding): Draft => ({
  brand_name: b.brand_name,
  login_tagline: b.login_tagline,
  login_layout: b.login_layout,
  login_focal: b.login_focal,
  login_overlay: b.login_overlay,
  login_image_url: b.login_image_url,
  login_image_id: b.login_image_id,
});

const same = (a: Draft, b: Draft) => (Object.keys(a) as (keyof Draft)[]).every((k) => a[k] === b[k]);

export function BrandingCard() {
  const qc = useQueryClient();
  const fileRef = React.useRef<HTMLInputElement>(null);

  const { data, isLoading } = useQuery({ queryKey: ["branding"], queryFn: api.branding });
  const { data: images = [] } = useQuery({
    queryKey: ["branding-images"],
    queryFn: api.brandingImages,
  });

  const [draft, setDraft] = React.useState<Draft | null>(null);

  // Adopt server state only while there is nothing unsaved to lose — otherwise
  // a background refetch would wipe edits mid-sentence.
  React.useEffect(() => {
    if (!data) return;
    setDraft((prev) => (prev === null || same(prev, toDraft(data)) ? toDraft(data) : prev));
  }, [data]);

  const dirty = Boolean(data && draft && !same(draft, toDraft(data)));
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) =>
    setDraft((d) => (d ? { ...d, [k]: v } : d));

  const adopt = (fresh: Branding) => {
    qc.setQueryData(["branding"], fresh);
    qc.invalidateQueries({ queryKey: ["branding-images"] });
    setDraft(toDraft(fresh));
  };

  const save = useMutation({
    mutationFn: (p: BrandingPayload) => api.updateBranding(p),
    onSuccess: (fresh) => {
      adopt(fresh);
      toast.success("Sign-in screen updated");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not save"),
  });

  const upload = useMutation({
    mutationFn: (f: File) => api.uploadBrandingImage(f),
    onSuccess: (fresh) => {
      adopt(fresh);
      toast.success("Image added");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Upload failed"),
  });

  const removeImage = useMutation({
    mutationFn: (id: string) => api.deleteBrandingImage(id),
    onSuccess: (fresh) => {
      adopt(fresh);
      toast.success("Image removed");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not remove"),
  });

  if (isLoading || !data || !draft) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sign-in screen</CardTitle>
        </CardHeader>
        <CardContent className="flex h-40 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    );
  }

  // The preview follows the draft, including which library image is picked, so
  // what is on screen is exactly what Save would publish.
  const previewSrc = draft.login_image_url || (draft.login_image_id ? brandingThumbUrl(draft.login_image_id) : "");
  const split = draft.login_layout === "split-right" || draft.login_layout === "split-left";
  const imageRight = draft.login_layout !== "split-left";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Sign-in screen</CardTitle>
        <CardDescription>
          What operators see before they log in. Everyone sees this, so it is served without
          a token — keep it to artwork and a name.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-7">
        {/* ---- live preview ---- */}
        <div>
          <Label className="text-xs text-muted-foreground">Preview</Label>
          <div className="mt-2 overflow-hidden rounded-xl border border-border/70 bg-background">
            <div className="relative aspect-[16/7] w-full">
              {split ? (
                <div className={cn("grid h-full grid-cols-2", !imageRight && "[&>*:first-child]:order-2")}>
                  <div className="relative flex flex-col p-4">
                    <div className="text-[9px] font-semibold uppercase tracking-[0.28em] text-foreground/80">
                      {draft.brand_name || "ATHENA"}
                    </div>
                    <div className="flex flex-1 flex-col justify-center space-y-1.5">
                      <div className="text-[13px] font-semibold">Sign in</div>
                      <div className="truncate text-[9px] text-muted-foreground">
                        {draft.login_tagline}
                      </div>
                      <div className="mt-2 h-[6px] w-4/5 rounded-[2px] border border-border/70" />
                      <div className="h-[6px] w-4/5 rounded-[2px] border border-border/70" />
                      <div className="mt-1.5 h-[9px] w-2/5 rounded-[3px] bg-foreground/80" />
                    </div>
                    <div
                      className={cn(
                        "absolute inset-y-0 w-px bg-border/70",
                        imageRight ? "right-0" : "left-0",
                      )}
                    />
                  </div>
                  <PreviewArt src={previewSrc} draft={draft} />
                </div>
              ) : (
                <div className="relative h-full">
                  <PreviewArt src={previewSrc} draft={draft} />
                  <div className="absolute inset-0 flex items-center justify-center">
                    <div className="w-1/2 rounded-md border border-border/70 bg-background/85 p-3 backdrop-blur-sm">
                      <div className="text-[11px] font-semibold">Sign in</div>
                      <div className="mt-1.5 h-[6px] w-full rounded-[2px] border border-border/70" />
                      <div className="mt-1 h-[6px] w-full rounded-[2px] border border-border/70" />
                      <div className="mt-2 h-[8px] w-1/2 rounded-[3px] bg-foreground/80" />
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ---- layout ---- */}
        <div className="space-y-2">
          <Label className="text-xs text-muted-foreground">Layout</Label>
          <div className="grid gap-2 sm:grid-cols-4">
            {LAYOUTS.map((l) => {
              const active = draft.login_layout === l.id;
              return (
                <button
                  key={l.id}
                  type="button"
                  onClick={() => set("login_layout", l.id)}
                  className={cn(
                    "rounded-lg border-2 p-2 text-left transition-colors",
                    active ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50",
                  )}
                >
                  <LayoutThumb id={l.id} active={active} />
                  <div className="mt-2 text-[11px] font-medium">{l.label}</div>
                  <div className="text-[10px] leading-tight text-muted-foreground">{l.hint}</div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ---- artwork library ---- */}
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Label className="text-xs text-muted-foreground">
              Artwork {images.length > 0 && `· ${images.length} of 12`}
            </Label>
            <div className="flex items-center gap-2">
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/avif,image/gif"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload.mutate(f);
                  e.target.value = ""; // so re-picking the same file fires again
                }}
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => fileRef.current?.click()}
                disabled={upload.isPending || images.length >= 12}
              >
                {upload.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Upload className="h-4 w-4" />
                )}
                Upload
              </Button>
            </div>
          </div>

          {images.length === 0 ? (
            <div className="flex h-24 flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border text-center">
              <ImageIcon className="h-5 w-5 text-muted-foreground/40" />
              <span className="text-[11px] text-muted-foreground">
                No artwork yet — the sign-in screen uses its built-in backdrop
              </span>
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-6">
              {/* "None" is a first-class choice: it is how an operator goes back
                  to the built-in backdrop without deleting their uploads. */}
              <button
                type="button"
                onClick={() => set("login_image_id", "")}
                className={cn(
                  "group relative flex aspect-[4/3] items-center justify-center overflow-hidden rounded-lg border-2 transition-colors",
                  draft.login_image_id === ""
                    ? "border-primary"
                    : "border-border hover:border-muted-foreground/50",
                )}
                title="No artwork"
              >
                <ImageIcon className="h-4 w-4 text-muted-foreground/50" />
                {draft.login_image_id === "" && (
                  <span className="absolute right-1 top-1 rounded-full bg-primary p-0.5">
                    <Check className="h-2.5 w-2.5 text-primary-foreground" />
                  </span>
                )}
              </button>

              {images.map((img) => {
                const picked = draft.login_image_id === img.id;
                return (
                  <div key={img.id} className="group relative">
                    <button
                      type="button"
                      onClick={() => set("login_image_id", img.id)}
                      className={cn(
                        "block aspect-[4/3] w-full overflow-hidden rounded-lg border-2 transition-colors",
                        picked ? "border-primary" : "border-border hover:border-muted-foreground/50",
                      )}
                      title={`${img.content_type.replace("image/", "").toUpperCase()} · ${kb(img.bytes)}`}
                    >
                      <img
                        src={brandingThumbUrl(img.id)}
                        alt=""
                        loading="lazy"
                        className="h-full w-full object-cover"
                      />
                    </button>
                    {picked && (
                      <span className="pointer-events-none absolute right-1 top-1 rounded-full bg-primary p-0.5">
                        <Check className="h-2.5 w-2.5 text-primary-foreground" />
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => removeImage.mutate(img.id)}
                      disabled={removeImage.isPending}
                      title="Delete from library"
                      className="absolute left-1 top-1 rounded-md bg-background/85 p-1 opacity-0 transition-opacity hover:bg-destructive hover:text-destructive-foreground focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="b-url" className="text-[11px] text-muted-foreground">
              …or link one instead
            </Label>
            <Input
              id="b-url"
              placeholder="https://example.com/wallpaper.jpg"
              value={draft.login_image_url}
              onChange={(e) => set("login_image_url", e.target.value)}
            />
            {draft.login_image_url && (
              <p className="text-[11px] text-muted-foreground">
                A link is set, so it is used instead of anything in the library. Clear it to go
                back to your uploads.
              </p>
            )}
          </div>
        </div>

        {/* ---- framing ---- */}
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="b-focal" className="text-xs text-muted-foreground">
              Focal point
            </Label>
            <Select value={draft.login_focal} onValueChange={(v) => set("login_focal", v as Draft["login_focal"])}>
              <SelectTrigger id="b-focal">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FOCALS.map((f) => (
                  <SelectItem key={f} value={f} className="capitalize">
                    {f}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[11px] text-muted-foreground">
              Which part stays in frame when the panel crops the image.
            </p>
          </div>

          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between">
              <Label htmlFor="b-overlay" className="text-xs text-muted-foreground">
                Dim
              </Label>
              <span className="text-[11px] tabular-nums text-muted-foreground">
                {draft.login_overlay}%
              </span>
            </div>
            <input
              id="b-overlay"
              type="range"
              min={0}
              max={90}
              step={5}
              value={draft.login_overlay}
              onChange={(e) => set("login_overlay", Number(e.target.value))}
              className="h-9 w-full cursor-pointer accent-primary"
            />
            <p className="text-[11px] text-muted-foreground">
              A bright picture behind pale text is unreadable — this is the fix.
            </p>
          </div>
        </div>

        {/* ---- wording ---- */}
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="b-name" className="text-xs text-muted-foreground">
              Brand name
            </Label>
            <Input
              id="b-name"
              maxLength={48}
              value={draft.brand_name}
              onChange={(e) => set("brand_name", e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="b-tag" className="text-xs text-muted-foreground">
              Tagline
            </Label>
            <Input
              id="b-tag"
              maxLength={160}
              value={draft.login_tagline}
              onChange={(e) => set("login_tagline", e.target.value)}
            />
          </div>
        </div>

        <div className="flex items-center gap-3 border-t border-border/60 pt-4">
          <Button type="button" onClick={() => save.mutate(draft)} disabled={save.isPending || !dirty}>
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Save changes
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => setDraft(toDraft(data))}
            disabled={save.isPending || !dirty}
          >
            Discard
          </Button>
          <span className="text-[11px] text-muted-foreground">
            {dirty ? "Unsaved changes — the preview shows the draft" : "Everything is saved"}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

function PreviewArt({ src, draft }: { src: string; draft: Draft }) {
  return (
    <div className="relative overflow-hidden bg-muted">
      {src ? (
        <img
          src={src}
          alt=""
          className="h-full w-full object-cover"
          style={{ objectPosition: draft.login_focal }}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-[radial-gradient(120%_100%_at_70%_20%,hsl(var(--primary)/0.30),transparent_60%)]">
          <ImageIcon className="h-5 w-5 text-muted-foreground/40" />
        </div>
      )}
      <div className="absolute inset-0 bg-background" style={{ opacity: draft.login_overlay / 100 }} />
    </div>
  );
}
