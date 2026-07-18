// Typed API client with JWT handling.

const TOKEN_KEY = "vpn_panel_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    clearToken();
    // BASE_URL respects the (possibly secret) path the panel is served under.
    if (!path.includes("/auth/login")) window.location.href = `${import.meta.env.BASE_URL}login`;
    throw new ApiError(401, "Unauthorized");
  }
  if (res.status === 204) return undefined as T;

  let data: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail = (data as { detail?: string })?.detail || res.statusText || "Request failed";
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

// ---- Types ----
export interface User {
  id: number;
  username: string;
  password: string;
  quota_bytes: number;
  used_bytes: number;
  rate_up_kbps: number;
  rate_down_kbps: number;
  is_active: boolean;
  expires_at: string | null;
  created_at: string;
  last_seen: string | null;
  total_sessions: number;
  note: string;
  created_by_admin_id: number | null;
  created_by_username: string;
  is_expired: boolean;
  quota_exceeded: boolean;
  online: boolean;
  sub_token: string;
  outbound: string;
  l2tp_mode: string;   // "ipsec" | "raw"
}

export interface Me {
  id: number;
  username: string;
  role: "superadmin" | "admin";
  can_create_users: boolean;
  max_users: number;
}

export interface Admin {
  id: number;
  username: string;
  role: "superadmin" | "admin";
  is_active: boolean;
  can_create_users: boolean;
  max_users: number;
  created_at: string;
  last_login: string | null;
  note: string;
  user_count: number;
}

export interface AdminPayload {
  username?: string;
  password?: string;
  role?: string;
  is_active?: boolean;
  can_create_users?: boolean;
  max_users?: number;
  note?: string;
}

export interface Invite {
  id: number;
  token: string;
  role: string;
  can_create_users: boolean;
  max_users: number;
  note: string;
  created_at: string;
  expires_at: string | null;
  used: boolean;
}

export interface InvitePayload {
  role?: string;
  can_create_users?: boolean;
  max_users?: number;
  note?: string;
  expires_in_hours?: number;
}

export interface Session {
  username: string;
  ifname: string;
  ip: string;
  protocol: string;
  calling_station: string;
  uptime_seconds: number;
  rx_bytes: number;
  tx_bytes: number;
  rx_rate_bps: number;
  tx_rate_bps: number;
  state: string;
}

export interface TopUser {
  username: string;
  used_bytes: number;
  quota_bytes: number;
  online: boolean;
}

export interface QuotaUser {
  username: string;
  used_bytes: number;
  quota_bytes: number;
  percent: number;
  online: boolean;
}

export interface Stats {
  total_users: number;
  active_users: number;
  online_count: number;
  traffic_today_bytes: number;
  traffic_total_bytes: number;
  quota_warnings: number;
  expired_users: number;
  rx_rate_bps: number;
  tx_rate_bps: number;
  top_users: TopUser[];
  near_quota: QuotaUser[];
}

export interface TrafficPoint {
  ts: string;
  online_count: number;
  rx_bps: number;
  tx_bps: number;
}

export interface SystemStats {
  cpu_percent: number;
  mem_total: number;
  mem_used: number;
  mem_percent: number;
  disk_total: number;
  disk_used: number;
  disk_percent: number;
  net_rx_bps: number;
  net_tx_bps: number;
  load_1: number;
  load_5: number;
  load_15: number;
  uptime_seconds: number;
  hostname: string;
  kernel: string;
}

export interface ConnEvent {
  ts: string;
  username: string;
  in_octets: number;
  out_octets: number;
  total_octets: number;
  session_time: number;
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor: string;
  action: string;
  target: string;
  detail: string;
}

export interface Health {
  status: string;
  xl2tpd: boolean;
  ipsec: boolean;
  db: boolean;
  accounting_log: boolean;
  uptime_seconds: number;
}

export interface ServerSettings {
  vpn_psk: string;
  wan_iface: string;
  ppp_local_ip: string;
  ppp_pool: string;
  admin_username: string;
  chap_secrets: string;
  server_address: string;
  sstp_address: string;
  sub_address: string;
  l2tp_raw_address: string;
  l2tp_enabled: boolean;
  sstp_enabled: boolean;
}

export interface PanelSettingsPayload {
  server_address?: string;
  sstp_address?: string;
  sub_address?: string;
  l2tp_raw_address?: string;
  l2tp_enabled?: boolean;
  sstp_enabled?: boolean;
}

export interface UserPayload {
  username?: string;
  password?: string;
  quota_bytes?: number;
  rate_up_kbps?: number;
  rate_down_kbps?: number;
  is_active?: boolean;
  expires_at?: string | null;
  note?: string;
  outbound?: string;
  l2tp_mode?: string;
}

export interface OutboundStatus {
  id: string;
  name: string;
  kind: string;
  description: string;
  status: "up" | "down";
  egress_ip: string | null;
  users: number;
  active: number | null;   // live IPs currently routed (warp), null for direct
  is_default: boolean;
}

export interface WgStatus {
  enabled: boolean;
  user_id?: number;
  public_key?: string;
  address?: string;
  online?: boolean;
  created_at?: string;
}

export interface WgConfig {
  config: string;
  qr_svg: string;
  address: string;
  filename: string;
}

export interface BackupInfo {
  name: string;
  size: number;
  created_at: string;
}

export interface BackupList {
  backups: BackupInfo[];
  keep: number;
  dir: string;
}

export type BulkActionType = "enable" | "disable" | "delete" | "reset-quota";

// ---- Endpoints ----
export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; expires_in: number; username: string; role: string }>(
      "/api/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
    ),
  me: () => request<Me>("/api/auth/me"),
  inviteInfo: (token: string) =>
    request<{ role: string; valid: boolean; note: string }>(
      `/api/auth/invite/${encodeURIComponent(token)}`,
    ),
  acceptInvite: (token: string, username: string, password: string) =>
    request<{ access_token: string; expires_in: number; username: string; role: string }>(
      "/api/auth/invite/accept",
      { method: "POST", body: JSON.stringify({ token, username, password }) },
    ),
  changePassword: (current_password: string, new_password: string) =>
    request<{ detail: string }>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

  listUsers: () => request<User[]>("/api/users"),
  getUser: (id: number) => request<User>(`/api/users/${id}`),
  createUser: (p: UserPayload) =>
    request<User>("/api/users", { method: "POST", body: JSON.stringify(p) }),
  updateUser: (id: number, p: UserPayload) =>
    request<User>(`/api/users/${id}`, { method: "PUT", body: JSON.stringify(p) }),
  deleteUser: (id: number) => request<void>(`/api/users/${id}`, { method: "DELETE" }),
  resetQuota: (id: number) => request<User>(`/api/users/${id}/reset-quota`, { method: "POST" }),
  toggleUser: (id: number) => request<User>(`/api/users/${id}/toggle`, { method: "POST" }),
  bulk: (ids: number[], action: BulkActionType) =>
    request<{ action: string; affected: string[] }>("/api/users/bulk", {
      method: "POST",
      body: JSON.stringify({ ids, action }),
    }),

  listSessions: () => request<Session[]>("/api/sessions"),
  disconnect: (username: string) =>
    request<{ detail: string }>(`/api/sessions/${encodeURIComponent(username)}`, {
      method: "DELETE",
    }),

  listAdmins: () => request<Admin[]>("/api/admins"),
  createAdmin: (p: AdminPayload) =>
    request<Admin>("/api/admins", { method: "POST", body: JSON.stringify(p) }),
  updateAdmin: (id: number, p: AdminPayload) =>
    request<Admin>(`/api/admins/${id}`, { method: "PUT", body: JSON.stringify(p) }),
  deleteAdmin: (id: number) => request<void>(`/api/admins/${id}`, { method: "DELETE" }),
  listInvites: () => request<Invite[]>("/api/admins/invites"),
  createInvite: (p: InvitePayload) =>
    request<Invite>("/api/admins/invites", { method: "POST", body: JSON.stringify(p) }),
  revokeInvite: (id: number) =>
    request<void>(`/api/admins/invites/${id}`, { method: "DELETE" }),

  stats: () => request<Stats>("/api/stats"),
  health: () => request<Health>("/api/health"),
  system: () => request<SystemStats>("/api/system"),
  trafficHistory: (minutes = 60) =>
    request<TrafficPoint[]>(`/api/traffic/history?minutes=${minutes}`),
  events: (limit = 200) => request<ConnEvent[]>(`/api/events?limit=${limit}`),
  audit: (limit = 200) => request<AuditEntry[]>(`/api/audit?limit=${limit}`),
  settings: () => request<ServerSettings>("/api/settings"),
  updateSettings: (p: PanelSettingsPayload) =>
    request<ServerSettings>("/api/settings", { method: "PUT", body: JSON.stringify(p) }),
  outbounds: () => request<OutboundStatus[]>("/api/settings/outbounds"),

  wgStatus: (userId: number) => request<WgStatus>(`/api/wireguard/${userId}`),
  wgEnable: (userId: number) => request<WgStatus>(`/api/wireguard/${userId}/enable`, { method: "POST" }),
  wgDisable: (userId: number) => request<void>(`/api/wireguard/${userId}`, { method: "DELETE" }),
  wgConfig: (userId: number) => request<WgConfig>(`/api/wireguard/${userId}/config`),

  listBackups: () => request<BackupList>("/api/backups"),
  createBackup: () => request<BackupInfo>("/api/backups", { method: "POST" }),
  deleteBackup: (name: string) =>
    request<void>(`/api/backups/${encodeURIComponent(name)}`, { method: "DELETE" }),
  telegramLink: () => request<{ linked: boolean; chat_id: string }>("/api/backups/telegram/test", { method: "POST" }),
  telegramSend: (name: string) =>
    request<{ sent: boolean }>(`/api/backups/${encodeURIComponent(name)}/telegram`, { method: "POST" }),
  downloadBackup: async (name: string) => {
    const token = getToken();
    const res = await fetch(`/api/backups/${encodeURIComponent(name)}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new ApiError(res.status, "Download failed");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};
