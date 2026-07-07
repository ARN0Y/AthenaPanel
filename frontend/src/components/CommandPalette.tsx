import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ClipboardList,
  LayoutDashboard,
  Moon,
  Radio,
  ScrollText,
  Settings,
  Shield,
  Sun,
  User as UserIcon,
  Users,
} from "lucide-react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useTheme } from "@/hooks/useTheme";
import { useAuth } from "@/hooks/useAuth";
import { api } from "@/lib/api";

export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const navigate = useNavigate();
  const { setTheme } = useTheme();
  const { isSuperadmin } = useAuth();
  const { data: users = [] } = useQuery({
    queryKey: ["users"],
    queryFn: api.listUsers,
    enabled: open,
  });

  const go = (path: string) => {
    onOpenChange(false);
    navigate(path);
  };

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Search pages, users, actions…" />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>
        <CommandGroup heading="Navigation">
          <CommandItem onSelect={() => go("/")}>
            <LayoutDashboard /> Dashboard
          </CommandItem>
          <CommandItem onSelect={() => go("/users")}>
            <Users /> Users
          </CommandItem>
          <CommandItem onSelect={() => go("/sessions")}>
            <Radio /> Sessions
          </CommandItem>
          <CommandItem onSelect={() => go("/events")}>
            <ScrollText /> Events
          </CommandItem>
          {isSuperadmin && (
            <>
              <CommandItem onSelect={() => go("/admins")}>
                <Shield /> Admins
              </CommandItem>
              <CommandItem onSelect={() => go("/audit")}>
                <ClipboardList /> Audit Log
              </CommandItem>
            </>
          )}
          <CommandItem onSelect={() => go("/settings")}>
            <Settings /> Settings
          </CommandItem>
        </CommandGroup>
        <CommandGroup heading="Appearance">
          <CommandItem onSelect={() => { setTheme("dark"); onOpenChange(false); }}>
            <Moon /> Dark theme
          </CommandItem>
          <CommandItem onSelect={() => { setTheme("light"); onOpenChange(false); }}>
            <Sun /> Light theme
          </CommandItem>
        </CommandGroup>
        {users.length > 0 && (
          <CommandGroup heading="Users">
            {users.slice(0, 8).map((u) => (
              <CommandItem
                key={u.id}
                value={`user ${u.username}`}
                onSelect={() => go(`/users/${u.id}`)}
              >
                <UserIcon /> {u.username}
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  );
}
