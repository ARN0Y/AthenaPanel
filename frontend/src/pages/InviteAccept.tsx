import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, ShieldCheck, UserPlus } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";

export function InviteAccept() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const { setSession } = useAuth();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["invite", token],
    queryFn: () => api.inviteInfo(token),
    retry: false,
  });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirm) return toast.error("Passwords do not match");
    setSubmitting(true);
    try {
      const res = await api.acceptInvite(token, username, password);
      await setSession(res.access_token);
      toast.success("Welcome! Your admin account is ready.");
      navigate("/", { replace: true });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to accept invite");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm animate-in-up">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/15 ring-1 ring-primary/20">
            <UserPlus className="h-7 w-7 text-primary" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight">Admin invite</h1>
          <p className="mt-1 text-sm text-muted-foreground">Set up your operator account</p>
        </div>

        <Card className="shadow-xl">
          <CardHeader>
            <CardTitle className="text-base">Create your account</CardTitle>
            <CardDescription>
              {isLoading ? "Checking invite…" : data?.valid ? `Role: ${data.role}` : "This invite is invalid or expired."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {data?.valid ? (
              <form onSubmit={submit} className="space-y-4">
                <div className="space-y-2"><Label>Username</Label><Input value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus /></div>
                <div className="space-y-2"><Label>Password</Label><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></div>
                <div className="space-y-2"><Label>Confirm password</Label><Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required /></div>
                <Button type="submit" className="w-full" disabled={submitting}>
                  {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                  Create account
                </Button>
              </form>
            ) : (
              <Button className="w-full" variant="outline" onClick={() => navigate("/login")}>Go to login</Button>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
