import { AnimatePresence, motion } from "motion/react";
import { useDeferredValue, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { useAlerts, useDevices, useMarkKnown, useStats, type DeviceFilters } from "../api/hooks";
import { AlertList } from "../components/AlertList";
import { DeviceTable } from "../components/DeviceTable";
import { AlertTriangleIcon, BellIcon, CheckIcon, RadarIcon, SearchIcon, ShieldIcon } from "../components/icons";
import { SegmentedControl } from "../components/SegmentedControl";
import { useToast } from "../components/Toasts";
import { Button, Card, cx, EmptyState, ErrorNote, Skeleton } from "../components/ui";
import { isScanStale, pluralize, timeAgo } from "../lib/format";

type View = "all" | "online" | "offline" | "unknown";

const VIEW_FILTERS: Record<View, DeviceFilters> = {
  all: {},
  online: { status: "online" },
  offline: { status: "offline" },
  unknown: { known: false },
};

function StatCard({
  label,
  value,
  sub,
  tone,
  icon,
  index,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "ok" | "warn" | "danger";
  icon: ReactNode;
  index: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05, type: "spring", stiffness: 300, damping: 28 }}
      className="rounded-2xl border border-line bg-surface p-4 shadow-xs sm:p-5"
    >
      <div className="flex items-center justify-between text-sm text-muted">
        <span>{label}</span>
        <span
          className={cx(
            "rounded-lg p-1.5",
            tone === "ok" && "bg-ok/12 text-ok",
            tone === "warn" && "bg-warn/15 text-warn",
            tone === "danger" && "bg-danger/12 text-danger",
            !tone && "bg-raised text-muted",
          )}
        >
          {icon}
        </span>
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight tabular-nums sm:text-3xl">{value}</div>
      {sub && <div className="mt-1 truncate text-xs text-muted">{sub}</div>}
    </motion.div>
  );
}

function ScanStatus() {
  const { data: stats } = useStats();
  if (!stats) return <Skeleton className="h-5 w-64" />;
  const scan = stats.last_scan;
  const stale = isScanStale(stats.last_successful_scan_at, stats.scan_interval_minutes);
  return (
    <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted">
      <span className="font-mono">{stats.subnet ?? scan?.subnet ?? "subnet: auto"}</span>
      <span aria-hidden="true">·</span>
      {scan ? (
        scan.status === "failed" ? (
          <span className="text-danger">Last scan failed {timeAgo(scan.started_at)}: {scan.error}</span>
        ) : (
          <span>
            Scanned {timeAgo(scan.finished_at ?? scan.started_at)} · every {stats.scan_interval_minutes} min
          </span>
        )
      ) : (
        <span>No scans yet: start the scanner worker (see README)</span>
      )}
      {scan && stale && scan.status !== "failed" && <span className="text-warn">· scanner seems stalled</span>}
    </p>
  );
}

export function DashboardPage() {
  const { data: stats, isPending: statsPending } = useStats();
  const [view, setView] = useState<View>("all");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim());
  const filters = useMemo(() => ({ ...VIEW_FILTERS[view], search: deferredSearch || undefined }), [view, deferredSearch]);
  const devices = useDevices(filters);
  const alerts = useAlerts({ acknowledged: false });
  const markKnown = useMarkKnown();
  const { toast } = useToast();
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const deviceList = devices.data?.results ?? [];
  const visibleSelected = deviceList.filter((d) => selected.has(d.id) && !d.is_known).map((d) => d.id);

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll(ids: number[], select: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (select) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  function approveSelected() {
    markKnown.mutate(visibleSelected, {
      onSuccess: ({ marked }) => {
        toast(`Marked ${pluralize(marked, "device")} as known`);
        setSelected(new Set());
      },
      onError: (e) => toast(e.message, "error"),
    });
  }

  const d = stats?.devices;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Your network</h1>
        <div className="mt-1">
          <ScanStatus />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        {statsPending || !d ? (
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-28 rounded-2xl" />)
        ) : (
          <>
            <StatCard index={0} label="Online" icon={<RadarIcon size={16} />} tone="ok" value={d.online} sub={`of ${pluralize(d.total, "device")}`} />
            <StatCard
              index={1}
              label="Unknown"
              icon={<AlertTriangleIcon size={16} />}
              tone={d.unknown ? "warn" : undefined}
              value={d.unknown}
              sub={d.unknown_online ? `${d.unknown_online} online now` : "all approved"}
            />
            <StatCard
              index={2}
              label="Open alerts"
              icon={<BellIcon size={16} />}
              tone={stats.open_alerts ? "danger" : undefined}
              value={stats.open_alerts}
              sub={<Link to="/alerts" className="hover:text-accent">View all alerts →</Link>}
            />
            <StatCard
              index={3}
              label="Known"
              icon={<ShieldIcon size={16} />}
              value={d.total - d.unknown}
              sub="approved devices"
            />
          </>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="overflow-hidden lg:col-span-2">
          <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <SegmentedControl<View>
              label="Device filter"
              value={view}
              onChange={(v) => {
                setView(v);
                setSelected(new Set());
              }}
              options={[
                { value: "all", label: "All", count: d?.total },
                { value: "online", label: "Online", count: d?.online },
                { value: "offline", label: "Offline", count: d ? d.total - d.online : undefined },
                { value: "unknown", label: "Unknown", count: d?.unknown },
              ]}
            />
            <label className="relative block sm:w-64">
              <span className="sr-only">Search devices</span>
              <SearchIcon size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" />
              <input
                type="search"
                placeholder="Search name, IP, MAC, vendor"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-9 w-full rounded-lg border border-line bg-canvas pr-3 pl-9 text-sm outline-none focus:border-accent"
              />
            </label>
          </div>

          <AnimatePresence>
            {visibleSelected.length > 0 && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="flex items-center justify-between gap-3 border-b border-line bg-accent/8 px-4 py-2.5 text-sm sm:px-5">
                  <span>{pluralize(visibleSelected.length, "device")} selected</span>
                  <div className="flex gap-2">
                    <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
                      Clear
                    </Button>
                    <Button size="sm" variant="primary" loading={markKnown.isPending} onClick={approveSelected}>
                      <CheckIcon size={14} />
                      Mark as known
                    </Button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <ErrorNote error={devices.error} />
          {devices.isPending ? (
            <div className="space-y-3 p-5">
              {Array.from({ length: 6 }, (_, i) => (
                <Skeleton key={i} className="h-10" />
              ))}
            </div>
          ) : deviceList.length === 0 ? (
            <EmptyState icon={<RadarIcon />} title={deferredSearch ? "No matching devices" : "No devices here"}>
              {view === "unknown" && !deferredSearch
                ? "Every device on your network is approved."
                : deferredSearch
                  ? "Try a different search."
                  : "Devices appear after the first successful scan."}
            </EmptyState>
          ) : (
            <div className={cx("transition-opacity", devices.isPlaceholderData && "opacity-60")}>
              <DeviceTable devices={deviceList} selected={selected} onToggle={toggle} onToggleAll={toggleAll} />
            </div>
          )}
        </Card>

        <Card className="self-start overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-5 py-4">
            <h2 className="font-semibold">Open alerts</h2>
            <Link to="/alerts" className="text-sm text-muted hover:text-accent">
              All alerts
            </Link>
          </div>
          <ErrorNote error={alerts.error} />
          {alerts.isPending ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
            </div>
          ) : (
            <AlertList alerts={(alerts.data?.results ?? []).slice(0, 8)} compact emptyText="All clear" />
          )}
        </Card>
      </div>
    </div>
  );
}
