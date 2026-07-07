export function formatBytes(bytes: number, decimals = 1): string {
  if (!bytes || bytes <= 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB", "PB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return `${parseFloat((bytes / k ** i).toFixed(decimals))} ${sizes[i]}`;
}

export function formatBps(bitsPerSec: number, decimals = 1): string {
  if (!bitsPerSec || bitsPerSec <= 0) return "0 bps";
  const k = 1000;
  const sizes = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"];
  const i = Math.min(Math.floor(Math.log(bitsPerSec) / Math.log(k)), sizes.length - 1);
  return `${parseFloat((bitsPerSec / k ** i).toFixed(decimals))} ${sizes[i]}`;
}

export function gbToBytes(gb: number): number {
  return Math.round(gb * 1024 ** 3);
}
export function bytesToGb(bytes: number): number {
  return bytes / 1024 ** 3;
}
export function mbpsToKbps(mbps: number): number {
  return Math.round(mbps * 1000);
}
export function kbpsToMbps(kbps: number): number {
  return kbps / 1000;
}

export function formatRate(kbps: number): string {
  if (!kbps || kbps <= 0) return "∞";
  if (kbps >= 1000) return `${(kbps / 1000).toFixed(kbps % 1000 === 0 ? 0 : 1)} Mbps`;
  return `${kbps} Kbps`;
}

export function formatQuota(bytes: number): string {
  if (!bytes || bytes <= 0) return "∞";
  return formatBytes(bytes);
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "0s";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h) parts.push(`${h}h`);
  if (m) parts.push(`${m}m`);
  if (!d && !h) parts.push(`${s}s`);
  return parts.join(" ");
}

export function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const diff = Date.now() - then;
  const s = Math.floor(diff / 1000);
  if (s < 0) return "in future";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

export function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}
