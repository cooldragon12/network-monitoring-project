import { AnimatePresence, motion } from "motion/react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { useCreateHost, useHosts } from "../api/hooks";
import type { CreatedRemoteHost, RemoteHost } from "../api/types";
import { Dialog } from "../components/Dialog";
import { HostSetup } from "../components/HostSetup";
import { PlusIcon, RadarIcon, ServerIcon } from "../components/icons";
import { MetricBar } from "../components/MetricBar";
import { SecretDialog } from "../components/SecretDialog";
import { Badge, Button, cx, EmptyState, ErrorNote, Skeleton, StatusDot } from "../components/ui";
import { formatDateTime, formatDuration, timeAgo } from "../lib/format";

export function HostStatusBadge({ host }: { host: Pick<RemoteHost, "status" | "is_active"> }) {
  if (!host.is_active) return <Badge>Disabled</Badge>;
  if (host.status === "unknown") return <Badge tone="info">Waiting for first check-in</Badge>;
  return (
    <Badge tone={host.status === "online" ? "ok" : "danger"}>
      <StatusDot online={host.status === "online"} className="scale-75" />
      {host.status === "online" ? "Online" : "Offline"}
    </Badge>
  );
}

function HostCard({ host, index }: { host: RemoteHost; index: number }) {
  const down = host.checks.filter((c) => c.status === "down").length;
  const hasMetrics = host.cpu_percent !== null || host.mem_percent !== null || host.disk_percent !== null;
  return (
    <motion.li
      layout="position"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={{ delay: Math.min(index, 8) * 0.04, type: "spring", stiffness: 300, damping: 28 }}
    >
      <Link
        to={`/hosts/${host.id}`}
        className={cx(
          "block h-full rounded-2xl border border-line bg-surface p-5 shadow-xs transition-colors hover:border-accent/50",
          host.status === "offline" && host.is_active && "border-danger/40",
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate font-semibold">{host.name}</p>
            <p className="truncate text-xs text-muted">
              {host.reported_hostname || "—"}
              {host.last_ip && ` · ${host.last_ip}`}
            </p>
          </div>
          <span className={cx("rounded-lg p-1.5", host.is_scanner ? "bg-accent/12 text-accent" : "bg-raised text-muted")}>
            {host.is_scanner ? <RadarIcon size={16} /> : <ServerIcon size={16} />}
          </span>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          <HostStatusBadge host={host} />
          {host.is_scanner && <Badge tone="accent">LAN scanner</Badge>}
          {host.checks.length > 0 && (
            <Badge tone={down ? "danger" : "neutral"}>
              {down ? `${down} of ${host.checks.length} checks down` : `${host.checks.length} checks`}
            </Badge>
          )}
        </div>

        {hasMetrics && (
          <div className="mt-4 grid grid-cols-3 gap-3">
            <MetricBar label="CPU" value={host.cpu_percent} />
            <MetricBar label="Mem" value={host.mem_percent} />
            <MetricBar label="Disk" value={host.disk_percent} />
          </div>
        )}

        <p className="mt-4 text-xs text-muted" title={formatDateTime(host.last_checkin)}>
          {host.last_checkin ? `Checked in ${timeAgo(host.last_checkin)}` : "Never checked in"}
          {host.uptime_seconds !== null && ` · up ${formatDuration(host.uptime_seconds)}`}
        </p>
      </Link>
    </motion.li>
  );
}

function AddHostDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (h: CreatedRemoteHost) => void }) {
  const create = useCreateHost();
  const [name, setName] = useState("");
  const [isScanner, setIsScanner] = useState(false);
  const [interval, setInterval] = useState(120);

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(
      { name: name.trim(), is_scanner: isScanner, expected_interval_seconds: isScanner ? 30 : interval },
      {
        onSuccess: (host) => {
          setName("");
          setIsScanner(false);
          onCreated(host);
        },
      },
    );
  }

  return (
    <Dialog open={open} onClose={onClose} title="Add host" description="A machine that runs the scanner agent and/or the host agent.">
      <form onSubmit={submit} className="space-y-4">
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">Name</span>
          <input
            required
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. this-pc, raspberry-pi, nas"
            className="h-10 w-full rounded-lg border border-line bg-canvas px-3 text-sm outline-none focus:border-accent"
          />
        </label>
        <label className="flex items-start gap-3 rounded-lg border border-line p-3">
          <input type="checkbox" className="mt-0.5 size-4" checked={isScanner} onChange={(e) => setIsScanner(e.target.checked)} />
          <span className="text-sm">
            <span className="font-medium">LAN scanner</span>
            <span className="block text-muted">Runs the scanner agent and discovers devices on your network.</span>
          </span>
        </label>
        {!isScanner && (
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Expected check-in interval (seconds)</span>
            <input
              type="number"
              min={10}
              max={86400}
              value={interval}
              onChange={(e) => setInterval(Number(e.target.value))}
              className="h-10 w-full rounded-lg border border-line bg-canvas px-3 text-sm outline-none focus:border-accent"
            />
            <span className="text-xs text-muted">Alerts if no check-in for twice this long. The agent defaults to every 60 s.</span>
          </label>
        )}
        <ErrorNote error={create.error} />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={create.isPending} disabled={!name.trim()}>
            Create and show token
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export function HostsPage() {
  const hosts = useHosts();
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<CreatedRemoteHost | null>(null);
  const list = hosts.data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Hosts</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            Machines running an agent: LAN scanners, plus servers, a Pi or a NAS reporting health. You get an alert
            when one stops checking in.
          </p>
        </div>
        <Button variant="primary" onClick={() => setAdding(true)}>
          <PlusIcon size={16} /> Add host
        </Button>
      </div>

      <ErrorNote error={hosts.error} />
      {hosts.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-44 rounded-2xl" />
          ))}
        </div>
      ) : list.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-line">
          <EmptyState icon={<ServerIcon />} title="No hosts yet">
            Add this PC as a <strong>LAN scanner</strong> first, so device discovery starts.
          </EmptyState>
        </div>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <AnimatePresence initial={false}>
            {list.map((host, i) => (
              <HostCard key={host.id} host={host} index={i} />
            ))}
          </AnimatePresence>
        </ul>
      )}

      <AddHostDialog
        open={adding}
        onClose={() => setAdding(false)}
        onCreated={(host) => {
          setAdding(false);
          setCreated(host);
        }}
      />
      <SecretDialog secret={created?.token ?? null} title={`Token for "${created?.name ?? ""}"`} onClose={() => setCreated(null)}>
        {created && <HostSetup host={created} token={created.token} />}
      </SecretDialog>
    </div>
  );
}
