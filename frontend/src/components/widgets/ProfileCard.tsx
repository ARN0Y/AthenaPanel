import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Copy, Smartphone } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type User } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { buildProfile } from "@/lib/profile";

function ConfigBlock({ title, text }: { title: string; text: string }) {
  const [copied, setCopied] = React.useState(false);
  const copy = () => {
    // copyText, not navigator.clipboard: the panel is also served over plain
    // HTTP (raw-IP access), where navigator.clipboard is undefined.
    copyText(text)
      .then(() => {
        setCopied(true);
        toast.success(`${title} config copied`);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => toast.error("Copy failed"));
  };
  return (
    <div className="rounded-lg border bg-muted/30">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="text-sm font-medium">{title}</span>
        <Button variant="outline" size="sm" onClick={copy}>
          {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="whitespace-pre-wrap break-all px-3 py-2.5 font-mono text-xs leading-relaxed text-foreground/90">
        {text}
      </pre>
    </div>
  );
}

export function ProfileCard({ user }: { user: User }) {
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  if (!settings) return null;

  const { blocks, rawUnconfigured } = buildProfile(user, settings);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Smartphone className="h-4 w-4" /> Connection profile
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {rawUnconfigured && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-200/90">
            This account is set to <strong>L2TP raw</strong>, but no raw entry host is configured yet.
            Set “L2TP raw address” in Settings to hand out its profile.
          </div>
        )}
        {blocks.map((b) => (
          <ConfigBlock key={b.title} title={b.title} text={b.text} />
        ))}
        {!settings.l2tp_enabled && !settings.sstp_enabled && (
          <p className="text-sm text-muted-foreground">No protocol enabled (see Settings).</p>
        )}
      </CardContent>
    </Card>
  );
}
