import { NavLink } from "react-router-dom";
import { type ReactNode } from "react";
import {
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
  // Settings edits node-wide config (endpoints, PSK, outbounds, backups) and its
  // write API is superadmin-only — a reseller gets the values they need for
  // customer profiles through /api/settings, not this page.
  { to: "/settings", label: "Settings", icon: Settings, super: true },
  { to: "/admins", label: "Admins", icon: Shield, super: true },
  { to: "/audit", label: "Audit Log", icon: ClipboardList, super: true },
];

function BrandMark({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "relative flex shrink-0 items-center justify-center overflow-hidden rounded-xl bg-gradient-to-br from-indigo-500 via-primary to-violet-500 shadow-lg shadow-primary/30 ring-1 ring-white/15",
        className,
      )}
    >
      <span className="pointer-events-none absolute inset-x-0 top-0 h-1/2 bg-white/10" />
      <svg viewBox="0 0 24 24" fill="none" className="relative h-[19px] w-[19px]">
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
              isActive ? "text-primary" : "text-current group-hover:text-sidebar-foreground",
            )}
          />
          {!collapsed && <span className="truncate">{item.label}</span>}
        </>
      )}
    </NavLink>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
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
        <BrandMark className="h-9 w-9" />
        {!collapsed && (
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">Athena VPN</div>
            <div className="text-[11px] text-sidebar-foreground/45">Control Panel</div>
          </div>
        )}
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
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
    </div>
  );
}
