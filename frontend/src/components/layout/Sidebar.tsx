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
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/useAuth";

export const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true, super: false },
  { to: "/users", label: "Users", icon: Users, super: false },
  { to: "/sessions", label: "Sessions", icon: Radio, super: false },
  { to: "/events", label: "Events", icon: ScrollText, super: false },
  { to: "/admins", label: "Admins", icon: Shield, super: true },
  { to: "/audit", label: "Audit Log", icon: ClipboardList, super: true },
  { to: "/settings", label: "Settings", icon: Settings, super: false },
];

export function SidebarContent({
  collapsed,
  onNavigate,
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const { isSuperadmin } = useAuth();
  const items = NAV_ITEMS.filter((i) => !i.super || isSuperadmin);

  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className={cn("flex h-16 items-center gap-2.5 border-b border-white/10 px-5", collapsed && "justify-center px-0")}>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/20">
          <Activity className="h-5 w-5 text-primary" />
        </div>
        {!collapsed && (
          <div className="leading-tight">
            <div className="text-sm font-semibold">Athena VPN</div>
            <div className="text-[11px] text-sidebar-foreground/50">Control Panel</div>
          </div>
        )}
      </div>

      <nav className="flex-1 space-y-1 p-3">
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onNavigate}
            title={collapsed ? item.label : undefined}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                collapsed && "justify-center px-0",
                isActive
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-sidebar-foreground/70 hover:bg-white/5 hover:text-sidebar-foreground",
              )
            }
          >
            <item.icon className="h-[18px] w-[18px] shrink-0" />
            {!collapsed && <span>{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      {!collapsed && (
        <div className="border-t border-white/10 p-4 text-[11px] text-sidebar-foreground/40">
          L2TP / IPsec · v3.0
        </div>
      )}
    </div>
  );
}
