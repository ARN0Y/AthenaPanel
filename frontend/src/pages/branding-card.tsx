import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Image as ImageIcon, Loader2, Trash2, Upload } from "lucide-react";
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
import { ApiError, api, brandingImageUrl, type Branding, type BrandingPayload } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Sign-in screen appearance.
 *
 * Everything here is written to `app_settings`, so it survives a redeploy and
 * lands in the nightly dump. The artwork is a file rather than a column — see
 * backend/app/branding.py for why.
 */

const LAYOUTS: { id: Branding["login_layout"]; label: string; hint: string }[] = [
  { id: "split-right", label: "Split · right", hint: "Form left, artwork right" },
  { id: "split-left", label: "Split · left", hint: "Artwork left, form right" },
  { id: "centered", label: "Centered", hint: "Card floating on the artwork" },
  { id: "backdrop", label: "Backdrop", hint: "Same, with a heavier veil" },
];

const FOCALS: Branding["login_focal"][] = ["center", "top", "bottom", "left", "right"];

/** A small drawing of each arrangement — quicker to read than the words. */
function LayoutThumb({ id, active }: { id: Branding["login_layout"]; active: boolean }) {
  const art = <div className={cn("rounded-[2px]", active ? "bg-primary/45" : "bg-muted-foreground/25")} />;
  const form = (
    <div className="flex flex-col justify-center gap-[3px] px-1.5">
      <div className={cn("h-[3px] w-2/3 rounded-full", active ? "bg-primary/70" : "bg-muted-foreground/45")} />
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

export function BrandingCard() {
  const qc = useQueryClient();
  const fileRef = React.useRef<HTMLInputElement>(null);

  const { data, isLoading } = useQuery({ queryKey: ["branding"], queryFn: api.branding });

  // Text inputs are local until saved; the pickers apply immediately, because
  // for those the preview beside them IS the feedback.
  const [brandName, setBrandName] = React.useState("");
  const [tagline, setTagline] = React.useState("");
  const [imageUrl, setImageUrl] = React.useState("");
  const [touched, setTouched] = React.useState(false);

  React.useEffect(() => {
    if (!data) return;
    setBrandName(data.brand_name);
    setTagline(data.login_tagline);
    setImageUrl(data.login_image_url);
    setTouched(false);
  }, [data]);

  const save = useMutation({
    mutationFn: (p: BrandingPayload) => api.updateBranding(p),
    onSuccess: (fresh) => {
      qc.setQueryData(["branding"], fresh);
      setTouched(false);
      toast.success("Sign-in screen updated");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not save"),
  });

  const upload = useMutation({
    mutationFn: (f: File) => api.uploadBrandingImage(f),
    onSuccess: (fresh) => {
      qc.setQueryData(["branding"], fresh);
      toast.success("Image uploaded");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Upload failed"),
  });

  const removeImage = useMutation({
    mutationFn: () => api.deleteBrandingImage(),
    onSuccess: (fresh) => {
      qc.setQueryData(["branding"], fresh);
      toast.success("Image removed");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not remove"),
  });

  if (isLoading || !data) {
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

  const src = brandingImageUrl(data);
  const split = data.login_layout === "split-right" || data.login_layout === "split-left";
  const imageRight = data.login_layout !== "split-left";

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
                  <div className="relative flex flex-col justify-between p-4">
                    <div className="text-[9px] font-semibold uppercase tracking-[0.28em] text-foreground/80">
                      {brandName || "ATHENA"}
                    </div>
                    <div className="space-y-1.5">
                      <div className="text-[13px] font-semibold">Sign in</div>
                      <div className="truncate text-[9px] text-muted-foreground">{tagline}</div>
                      <div className="mt-2 h-[3px] w-4/5 rounded-full bg-muted-foreground/25" />
                      <div className="h-[3px] w-4/5 rounded-full bg-muted-foreground/25" />
                      <div className="mt-1.5 h-[9px] w-2/5 rounded-[3px] bg-foreground/80" />
                    </div>
                    <div className="text-[7px] uppercase tracking-widest text-muted-foreground/50">
                      Athena Panel
                    </div>
                    <div
                      className={cn(
                        "absolute inset-y-0 w-px bg-border/70",
                        imageRight ? "right-0" : "left-0",
                      )}
                    />
                  </div>
                  <PreviewArt src={src} brand={data} />
                </div>
              ) : (
                <div className="relative h-full">
                  <PreviewArt src={src} brand={data} />
                  <div className="absolute inset-0 flex items-center justify-center">
                    <div className="w-1/2 rounded-md border border-border/70 bg-background/85 p-3 backdrop-blur-sm">
                      <div className="text-[11px] font-semibold">Sign in</div>
                      <div className="mt-1.5 h-[3px] w-full rounded-full bg-muted-foreground/30" />
                      <div className="mt-1 h-[3px] w-full rounded-full bg-muted-foreground/30" />
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
              const active = data.login_layout === l.id;
              return (
                <button
                  key={l.id}
                  type="button"
                  onClick={() => save.mutate({ login_layout: l.id })}
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

        {/* ---- artwork ---- */}
        <div className="space-y-3">
          <Label className="text-xs text-muted-foreground">Artwork</Label>
          <div className="flex flex-wrap items-center gap-2">
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
              disabled={upload.isPending}
            >
              {upload.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              Upload image
            </Button>
            {data.has_image && !data.login_image_url && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => removeImage.mutate()}
                disabled={removeImage.isPending}
              >
                <Trash2 className="h-4 w-4" />
                Remove
              </Button>
            )}
            <span className="text-[11px] text-muted-foreground">
              PNG, JPEG, WebP, AVIF or GIF · up to 12 MB
            </span>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="b-url" className="text-[11px] text-muted-foreground">
              …or link one instead
            </Label>
            <div className="flex gap-2">
              <Input
                id="b-url"
                placeholder="https://example.com/wallpaper.jpg"
                value={imageUrl}
                onChange={(e) => {
                  setImageUrl(e.target.value);
                  setTouched(true);
                }}
              />
              <Button
                type="button"
                variant="outline"
                onClick={() => save.mutate({ login_image_url: imageUrl })}
                disabled={save.isPending}
              >
                Apply
              </Button>
            </div>
            {data.login_image_url && (
              <p className="text-[11px] text-muted-foreground">
                A link is set, so it is used instead of any uploaded file. Clear it to go back
                to the upload.
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
            <Select
              value={data.login_focal}
              onValueChange={(v) => save.mutate({ login_focal: v })}
            >
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
                {data.login_overlay}%
              </span>
            </div>
            <input
              id="b-overlay"
              type="range"
              min={0}
              max={90}
              step={5}
              value={data.login_overlay}
              onChange={(e) => save.mutate({ login_overlay: Number(e.target.value) })}
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
              value={brandName}
              onChange={(e) => {
                setBrandName(e.target.value);
                setTouched(true);
              }}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="b-tag" className="text-xs text-muted-foreground">
              Tagline
            </Label>
            <Input
              id="b-tag"
              maxLength={160}
              value={tagline}
              onChange={(e) => {
                setTagline(e.target.value);
                setTouched(true);
              }}
            />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Button
            type="button"
            onClick={() =>
              save.mutate({
                brand_name: brandName,
                login_tagline: tagline,
                login_image_url: imageUrl,
              })
            }
            disabled={save.isPending || !touched}
          >
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Save text
          </Button>
          {touched && <span className="text-[11px] text-muted-foreground">Unsaved changes</span>}
        </div>
      </CardContent>
    </Card>
  );
}

function PreviewArt({ src, brand }: { src: string; brand: Branding }) {
  return (
    <div className="relative overflow-hidden bg-muted">
      {src ? (
        <img
          src={src}
          alt=""
          className="h-full w-full object-cover"
          style={{ objectPosition: brand.login_focal }}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-[radial-gradient(120%_100%_at_70%_20%,hsl(var(--primary)/0.30),transparent_60%)]">
          <ImageIcon className="h-5 w-5 text-muted-foreground/40" />
        </div>
      )}
      <div className="absolute inset-0 bg-background" style={{ opacity: brand.login_overlay / 100 }} />
    </div>
  );
}
