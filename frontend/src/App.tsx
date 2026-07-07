import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { ProtectedRoute, SuperadminRoute } from "@/components/ProtectedRoute";
import { Admins } from "@/pages/Admins";
import { Audit } from "@/pages/Audit";
import { Dashboard } from "@/pages/Dashboard";
import { Events } from "@/pages/Events";
import { InviteAccept } from "@/pages/InviteAccept";
import { Login } from "@/pages/Login";
import { Sessions } from "@/pages/Sessions";
import { Settings } from "@/pages/Settings";
import { UserDetail } from "@/pages/UserDetail";
import { Users } from "@/pages/Users";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/invite/:token" element={<InviteAccept />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/users" element={<Users />} />
          <Route path="/users/:id" element={<UserDetail />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/events" element={<Events />} />
          <Route element={<SuperadminRoute />}>
            <Route path="/admins" element={<Admins />} />
            <Route path="/audit" element={<Audit />} />
          </Route>
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
