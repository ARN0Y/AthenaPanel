import * as React from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Loader2 } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/useAuth";
import { ApiError, api, brandingImageUrl, type Branding } from "@/lib/api";

/**
 * The sign-in screen.
 *
 * Its appearance comes from `GET /api/branding`, which is public because this
 * page renders before anyone has a token. The whole component is built to work
 * with that request failing: `FALLBACK` is a complete, presentable
 * configuration, so a backend that is mid-deploy, or older than this build,
 * shows a plain panel rather than an empty one.
 */

const FALLBACK: Branding = {
  brand_name: "ATHENA",
  login_tagline: "Operator access to the control plane.",
  login_layout: "split-right",
  login_focal: "center",
  login_overlay: 45,
  login_image_url: "",
  has_image: false,
  login_image_version: "0",
};

function BrandMark({ name }: { name: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="relative flex h-7 w-7 items-center justify-center">
        <span className="absolute inset-0 rotate-45 rounded-[7px] border border-foreground/25" />
        <span className="absolute inset-[9px] rotate-45 rounded-[2px] bg-foreground/80" />
      </span>
      <span className="text-[13px] font-semibold uppercase tracking-[0.28em] text-foreground/90">
        {name}
      </span>
    </div>
  );
}

/** The artwork panel. Also the centred/backdrop variants' background. */
function Artwork({
  brand,
  className = "",
  rounded = false,
}: {
  brand: Branding;
  className?: string;
  rounded?: boolean;
}) {
  const src = brandingImageUrl(brand);
  // Tracked so a broken external URL falls back to the gradient instead of
  // leaving a dead image frame on the page.
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [src]);

  const showImage = Boolean(src) && !failed;

  return (
    <div className={`overflow-hidden bg-muted ${rounded ? "rounded-2xl" : ""} ${className}`}>
      {showImage ? (
        <img
          src={src}
          alt=""
          aria-hidden="true"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
          style={{ objectPosition: brand.login_focal }}
        />
      ) : (
        // The built-in look, used when no artwork is set. Deliberately quiet:
        // it should read as intentional, not as a missing image.
        <div className="absolute inset-0 bg-[radial-gradient(120%_100%_at_70%_20%,hsl(var(--primary)/0.30),transparent_60%),radial-gradient(90%_80%_at_20%_90%,hsl(var(--primary)/0.14),transparent_55%)]">
          <div
            className="absolute inset-0 opacity-40"
            style={{
              backgroundImage:
                "radial-gradient(hsl(var(--foreground)/0.10) 1px, transparent 1px)",
              backgroundSize: "26px 26px",
            }}
          />
        </div>
      )}
      {/* Dim layer. The operator picks the picture, so the panel cannot assume
          it is dark enough for white text — this is what keeps it readable. */}
      <div
        className="absolute inset-0 bg-background"
        style={{ opacity: brand.login_overlay / 100 }}
      />
      {/* A gradient toward the form side, so the seam never looks like a hard cut. */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-background/70 via-transparent to-transparent" />
    </div>
  );
}

function SignInCard({
  brand,
  onSubmit,
  loading,
  username,
  setUsername,
  password,
  setPassword,
  standalone,
}: {
  brand: Branding;
  onSubmit: (e: React.FormEvent) => void;
  loading: boolean;
  username: string;
  setUsername: (v: string) => void;
  password: string;
  setPassword: (v: string) => void;
  standalone: boolean;
}) {
  return (
    <div
      className={
        standalone
          ? "w-full max-w-[400px] rounded-2xl border border-border/60 bg-card/85 p-7 shadow-2xl shadow-black/30 backdrop-blur-md"
          : "w-full max-w-[380px]"
      }
    >
      <h1 className="text-[26px] font-semibold tracking-tight">Sign in</h1>
      {brand.login_tagline && (
        <p className="mt-1.5 text-sm text-muted-foreground">{brand.login_tagline}</p>
      )}

      <form onSubmit={onSubmit} className="mt-8 space-y-4">
        <div className="space-y-2">
          <Label
            htmlFor="username"
            className="text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground"
          >
            Username
          </Label>
          <Input
            id="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoFocus
            className="h-11 rounded-lg border border-border/70 bg-background/60 px-3.5 text-[15px] transition-colors placeholder:text-muted-foreground/50 focus-visible:border-primary/70 focus-visible:bg-background focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:ring-offset-0"
            placeholder="admin"
          />
        </div>

        <div className="space-y-2">
          <div className="flex items-baseline justify-between">
            <Label
              htmlFor="password"
              className="text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground"
            >
              Password
            </Label>
          </div>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="h-11 rounded-lg border border-border/70 bg-background/60 px-3.5 text-[15px] transition-colors placeholder:text-muted-foreground/50 focus-visible:border-primary/70 focus-visible:bg-background focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:ring-offset-0 tracking-[0.18em] placeholder:tracking-normal"
            placeholder="••••••••"
          />
        </div>

        <Button
          type="submit"
          disabled={loading}
          className="group mt-2 h-11 w-full rounded-lg text-[11px] font-semibold uppercase tracking-[0.18em]"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <>
              Continue
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
            </>
          )}
        </Button>
      </form>
    </div>
  );
}

export function Login() {
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [loading, setLoading] = React.useState(false);

  const { data } = useQuery({
    queryKey: ["branding"],
    queryFn: api.branding,
    staleTime: 5 * 60 * 1000,
    retry: false, // never leave the sign-in form waiting on decoration
  });
  const brand: Branding = { ...FALLBACK, ...(data ?? {}) };

  React.useEffect(() => {
    if (isAuthenticated) navigate("/", { replace: true });
  }, [isAuthenticated, navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await login(username, password);
      navigate("/", { replace: true });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const card = (standalone: boolean) => (
    <SignInCard
      brand={brand}
      standalone={standalone}
      onSubmit={handleSubmit}
      loading={loading}
      username={username}
      setUsername={setUsername}
      password={password}
      setPassword={setPassword}
    />
  );

  // ---- centred / backdrop: one column, artwork behind everything ----
  if (brand.login_layout === "centered" || brand.login_layout === "backdrop") {
    return (
      <div className="relative flex min-h-screen items-center justify-center overflow-hidden p-6">
        <Artwork brand={brand} className="absolute inset-0" />
        <div className="relative flex w-full max-w-[400px] flex-col items-center">
          <div className="mb-8">
            <BrandMark name={brand.brand_name} />
          </div>
          {card(true)}
        </div>
      </div>
    );
  }

  // ---- split: form on one side, artwork on the other, a rule between ----
  const imageRight = brand.login_layout !== "split-left";

  return (
    <div className="relative min-h-screen bg-background">
      <div
        className={`grid min-h-screen lg:grid-cols-2 ${
          imageRight ? "" : "lg:[&>*:first-child]:order-2"
        }`}
      >
        {/* Form side */}
        <div className="relative flex flex-col justify-between px-6 py-10 sm:px-12 lg:px-16 xl:px-24">
          <BrandMark name={brand.brand_name} />
          <div className="flex flex-1 items-center py-12">{card(false)}</div>

          {/* The rule the operator asked for. Only on the seam between the two
              columns, and only once they are actually side by side. */}
          <div
            className={`pointer-events-none absolute inset-y-0 hidden w-px bg-border/70 lg:block ${
              imageRight ? "right-0" : "left-0"
            }`}
          />
        </div>

        {/* Artwork side. Hidden on narrow screens: a cropped sliver of a
            wallpaper above a form is worse than no wallpaper. */}
        <Artwork brand={brand} className="relative hidden lg:block" />
      </div>
    </div>
  );
}
