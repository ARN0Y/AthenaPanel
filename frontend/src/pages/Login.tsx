import * as React from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Loader2, Lock, User } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/useAuth";
import { ApiError } from "@/lib/api";

function BrandMark() {
  return (
    <div className="relative flex h-14 w-14 items-center justify-center overflow-hidden rounded-2xl bg-gradient-to-br from-indigo-500 via-primary to-violet-500 shadow-xl shadow-primary/30 ring-1 ring-white/15">
      <span className="pointer-events-none absolute inset-x-0 top-0 h-1/2 bg-white/10" />
      <svg viewBox="0 0 24 24" fill="none" className="relative h-7 w-7">
        <path
          d="M12 2.6 4.7 5.4v5.1c0 4.5 3.1 8.2 7.3 9.6 4.2-1.4 7.3-5.1 7.3-9.6V5.4L12 2.6Z"
          fill="white"
          fillOpacity="0.16"
          stroke="white"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
        <path
          d="M7.7 12.7h2.1l1.2-3.1 1.7 5.2 1.1-2.1h2.4"
          stroke="white"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

export function Login() {
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    if (isAuthenticated) navigate("/", { replace: true });
  }, [isAuthenticated, navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await login(username, password);
      toast.success("Welcome back");
      navigate("/", { replace: true });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background p-4">
      {/* Ambient depth */}
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-[-10%] h-[38rem] w-[38rem] -translate-x-1/2 rounded-full bg-primary/10 blur-[120px]" />
        <div className="absolute bottom-[-20%] right-[-10%] h-[30rem] w-[30rem] rounded-full bg-violet-500/10 blur-[120px]" />
        <div
          className="absolute inset-0 opacity-[0.4]"
          style={{
            backgroundImage:
              "radial-gradient(hsl(var(--foreground)/0.05) 1px, transparent 1px)",
            backgroundSize: "22px 22px",
            maskImage: "radial-gradient(ellipse at center, black, transparent 72%)",
          }}
        />
      </div>

      <div className="relative w-full max-w-[380px] animate-in-up">
        <div className="mb-7 flex flex-col items-center text-center">
          <BrandMark />
          <h1 className="mt-4 text-[22px] font-semibold tracking-tight">Athena VPN</h1>
          <p className="mt-1 text-sm text-muted-foreground">Sign in to continue</p>
        </div>

        <div className="rounded-2xl border border-border/60 bg-card/80 p-6 shadow-2xl shadow-black/20 backdrop-blur-sm sm:p-7">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="username" className="text-xs text-muted-foreground">
                Username
              </Label>
              <div className="relative">
                <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="username"
                  className="h-11 pl-9"
                  autoComplete="username"
                  placeholder="admin"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  autoFocus
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password" className="text-xs text-muted-foreground">
                Password
              </Label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="password"
                  type="password"
                  className="h-11 pl-9"
                  autoComplete="current-password"
                  placeholder="••••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </div>
            </div>
            <Button type="submit" className="group h-11 w-full text-[13px] font-semibold" disabled={loading}>
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  Sign in
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
                </>
              )}
            </Button>
          </form>
        </div>

        <div className="mt-6 flex items-center justify-center gap-2 text-[11px] text-muted-foreground/70">
          <span className="h-1 w-1 rounded-full bg-success" />
          Athena Panel · v3.0
        </div>
      </div>
    </div>
  );
}
