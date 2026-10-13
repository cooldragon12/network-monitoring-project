import { LayoutGroup, motion } from "motion/react";
import { Navigate, NavLink, Outlet, useLocation } from "react-router";

import { useLogout, useSession, useStats } from "../api/hooks";
import { BellIcon, GridIcon, KeyIcon, LogOutIcon, LogoIcon } from "./icons";
import { cx, Spinner } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: GridIcon, end: true },
  { to: "/alerts", label: "Alerts", icon: BellIcon, end: false },
  { to: "/tokens", label: "API tokens", icon: KeyIcon, end: false },
];

/** ``group`` scopes the sliding pill: desktop and mobile navs are both mounted. */
function NavItems({ group }: { group: string }) {
  const { data: stats } = useStats();
  return (
    <LayoutGroup id={group}>
    <nav aria-label="Main" className="flex gap-1">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            cx(
              "relative flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors",
              isActive ? "text-fg" : "text-muted hover:text-fg",
            )
          }
        >
          {({ isActive }) => (
            <>
              {isActive && (
                <motion.span
                  layoutId="nav-pill"
                  className="absolute inset-0 rounded-lg bg-raised"
                  transition={{ type: "spring", stiffness: 500, damping: 40 }}
                />
              )}
              <Icon size={16} className="relative" />
              <span className="relative">{label}</span>
              {to === "/alerts" && !!stats?.open_alerts && (
                <span className="relative rounded-full bg-danger px-1.5 text-xs font-semibold text-white tabular-nums">
                  {stats.open_alerts}
                </span>
              )}
            </>
          )}
        </NavLink>
      ))}
    </nav>
    </LayoutGroup>
  );
}

export function Layout() {
  const { data: session } = useSession();
  const logout = useLogout();
  const location = useLocation();

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b border-line bg-canvas/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2 font-semibold">
            <span className="rounded-lg bg-accent/15 p-1.5 text-accent">
              <LogoIcon size={18} />
            </span>
            <span className="hidden sm:inline">Network Monitor</span>
          </div>
          <div className="hidden flex-1 md:block">
            <NavItems group="desktop" />
          </div>
          <div className="ml-auto flex items-center gap-2 text-sm text-muted">
            <span className="hidden sm:inline">{session?.user?.username}</span>
            <button
              type="button"
              onClick={() => logout.mutate()}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 hover:bg-raised hover:text-fg"
            >
              <LogOutIcon size={16} />
              <span>Sign out</span>
            </button>
          </div>
        </div>
        <div className="overflow-x-auto border-t border-line px-3 py-1.5 md:hidden">
          <NavItems group="mobile" />
        </div>
      </header>

      <motion.main
        key={location.pathname}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: [0.2, 0.8, 0.2, 1] }}
        className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8"
      >
        <Outlet />
      </motion.main>
    </div>
  );
}

/** Gate for authenticated routes; sends anonymous users to /login. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { data: session, isPending } = useSession();
  const location = useLocation();
  if (isPending) {
    return (
      <div className="grid min-h-dvh place-items-center text-muted">
        <Spinner size={24} />
      </div>
    );
  }
  if (!session?.authenticated) {
    const next = location.pathname + location.search;
    return <Navigate to={`/login${next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`} replace />;
  }
  return <>{children}</>;
}
