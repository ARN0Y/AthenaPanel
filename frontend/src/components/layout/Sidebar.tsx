import { NavLink } from "react-router-dom";
import {
  Activity,
  ClipboardList,
  LayoutDashboard,
  Radio,
  ScrollText,
  Settings,
  Shield,
  Users,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/useAuth";

type NavItemDef = {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  super?: boolean;
};

export const NAV_ITEMS: NavItemDef[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true, super: false },
  { to: "/users", label: "Users", icon: Users, super: false },
  { to: "/sessions", label: "Sessions", icon: Radio, super: false },
  { to: "/events", label: "Events", icon: ScrollText, super: false },
  { to: "/settings", label: "Settings", icon: Settings, super: false },
  { to: "/admins", label: "Admins", icon: Shield, super: true },
  { to: "/audit", label: "Audit Log", icon: ClipboardList, super: true },
];

function NavItem({
  item,
  collapsed,
  onNavigate,
}: {
  item: NavItemDef;
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) =>
        cn(
          "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium outline-none transition-colors",
          "focus-visible:ring-2 focus-visible:ring-ring/60",
          collapsed && "justify-center px-0",
          isActive
            ? "bg-primary/10 text-foreground"
            : "text-sidebar-foreground/55 hover:bg-foreground/[0.045] hover:text-sidebar-foreground",
        )
      }
    >
      {({ isActive }) => (
        <>
          <span
            className={cn(
              "absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-primary transition-opacity duration-200",
              isActive && !collapsed ? "opacity-100" : "opacity-0",
            )}
          />
          <item.icon
            className={cn(
              "h-[18px] w-[18px] shrink-0 transition-colors",
              isActive
                ? "text-primary"
                : "text-current group-hover:text-sidebar-foreground",
            )}
          />
          {!collapsed && <span className="truncate">{item.label}</span>}
        </>
      )}
    </NavLink>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-3 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-[0.14em] text-sidebar-foreground/35">
      {children}
    </p>
  );
}

export function SidebarContent({
  collapsed,
  onNavigate,
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const { isSuperadmin } = useAuth();
  const mainItems = NAV_ITEMS.filter((i) => !i.super);
  const adminItems = NAV_ITEMS.filter((i) => i.super);

  return (
    <div className="flex h-full flex-col border-r border-border/70 bg-sidebar text-sidebar-foreground">
      {/* Brand */}
      <div className={cn("flex h-16 items-center gap-3 px-5", collapsed && "justify-center px-0")}>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-indigo-400 shadow-lg shadow-primary/25 ring-1 ring-white/10">
          <Activity className="h-[18px] w-[18px] text-white" />
        </div>
        {!collapsed && (
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">Athena VPN</div>
            <div className="text-[11px] text-sidebar-foreground/45">Control Panel</div>
          </div>
        )}
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-3">
        {!collapsed ? <SectionLabel>Overview</SectionLabel> : <div className="h-2" />}
        {mainItems.map((item) => (
          <NavItem key={item.to} item={item} collapsed={collapsed} onNavigate={onNavigate} />
        ))}

        {isSuperadmin && adminItems.length > 0 && (
          <>
            {!collapsed ? (
              <SectionLabel>Administration</SectionLabel>
            ) : (
              <div className="mx-auto my-2 h-px w-6 bg-border" />
            )}
            {adminItems.map((item) => (
              <NavItem key={item.to} item={item} collapsed={collapsed} onNavigate={onNavigate} />
            ))}
          </>
        )}
      </nav>

      {/* Status footer */}
      {!collapsed ? (
        <div className="p-3">
          <div className="flex items-center gap-2.5 rounded-lg bg-foreground/[0.035] px-3 py-2.5 ring-1 ring-inset ring-border/60">
            <span className="relative flex h-2 w-2 shrink-0">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success/60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
            </span>
            <div className="min-w-0 leading-tight">
              <div className="truncate text-xs font-medium text-sidebar-foreground/85">
                L2TP · SSTP · WireGuard
              </div>
              <div className="text-[10px] text-sidebar-foreground/40">Athena v3.0 · online</div>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex justify-center py-4">
          <span className="h-2 w-2 rounded-full bg-success" />
        </div>
      )}
    </div>
  );
}
