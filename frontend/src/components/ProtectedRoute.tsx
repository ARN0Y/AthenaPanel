import { Navigate, Outlet } from "react-router-dom";

import { useAuth } from "@/hooks/useAuth";

function Loading() {
  return (
    <div className="flex min-h-screen items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}

export function ProtectedRoute() {
  const { isAuthenticated, loading } = useAuth();
  if (loading) return <Loading />;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <Outlet />;
}

export function SuperadminRoute() {
  const { isAuthenticated, loading, isSuperadmin } = useAuth();
  if (loading) return <Loading />;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  if (!isSuperadmin) return <Navigate to="/" replace />;
  return <Outlet />;
}
