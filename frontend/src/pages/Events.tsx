import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDownToLine, ArrowUpFromLine, RefreshCw, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/widgets/PageHeader";
import { api } from "@/lib/api";
import { formatBytes, formatDate, formatDuration } from "@/lib/format";

export function Events() {
  const { data: events = [], isLoading, isFetching, refetch } = useQuery({
    queryKey: ["events", 500],
    queryFn: () => api.events(500),
    refetchInterval: 15000,
  });
  const [search, setSearch] = React.useState("");

  const filtered = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? events.filter((e) => e.username.toLowerCase().includes(q)) : events;
  }, [events, search]);

  const totalBytes = filtered.reduce((a, e) => a + e.total_octets, 0);

  return (
    <div>
      <PageHeader
        title="Connection events"
        description="History of finished sessions (from accounting log)"
        actions={
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        {[
          { label: "Events shown", value: String(filtered.length) },
          { label: "Total traffic", value: formatBytes(totalBytes) },
          { label: "Unique users", value: String(new Set(filtered.map((e) => e.username)).size) },
        ].map((s) => (
          <Card key={s.label}>
            <CardContent className="p-4">
              <div className="text-2xl font-bold tabular-nums">{s.value}</div>
              <div className="text-xs text-muted-foreground">{s.label}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center gap-3 border-b p-4">
            <div className="relative min-w-[200px] flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input placeholder="Search user…" className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            <Badge variant="outline" className="ml-auto">{filtered.length} events</Badge>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead className="text-right">↑ Upload</TableHead>
                <TableHead className="text-right">↓ Download</TableHead>
                <TableHead className="text-right">Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading && (
                <TableRow><TableCell colSpan={6} className="py-10 text-center text-muted-foreground">Loading…</TableCell></TableRow>
              )}
              {!isLoading && filtered.length === 0 && (
                <TableRow><TableCell colSpan={6} className="py-10 text-center text-muted-foreground">No events match.</TableCell></TableRow>
              )}
              {filtered.map((e, i) => (
                <TableRow key={i}>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDate(e.ts)}</TableCell>
                  <TableCell className="font-medium">{e.username}</TableCell>
                  <TableCell className="text-sm">{formatDuration(e.session_time)}</TableCell>
                  <TableCell className="text-right text-xs">
                    <span className="inline-flex items-center gap-1"><ArrowUpFromLine className="h-3 w-3 text-[hsl(var(--chart-tx))]" />{formatBytes(e.in_octets)}</span>
                  </TableCell>
                  <TableCell className="text-right text-xs">
                    <span className="inline-flex items-center gap-1"><ArrowDownToLine className="h-3 w-3 text-[hsl(var(--chart-rx))]" />{formatBytes(e.out_octets)}</span>
                  </TableCell>
                  <TableCell className="text-right text-sm font-medium">{formatBytes(e.total_octets)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
