import { AnimatePresence, motion } from "motion/react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError } from "../api/client";
import {
  useAlerts,
  useCreateCheck,
  useDeleteCheck,
  useDeleteHost,
  useHost,
  useHostEvents,
  useRotateHostToken,
  useUpdateCheck,
  useUpdateHost,
} from "../api/hooks";
import type { CheckKind, HostCheck, HostEvent, RemoteHost } from "../api/types";
import { AlertList } from "../components/AlertList";
import { ConfirmDialog } from "../components/Dialog";
import { HostSetup } from "../components/HostSetup";
import { ArrowLeftIcon, PlusIcon, RefreshIcon, TrashIcon } from "../components/icons";
import { MetricBar } from "../components/MetricBar";
import { SecretDialog } from "../components/SecretDialog";
import { useToast } from "../components/Toasts";
import { Badge, Button, Card, cx, EmptyState, ErrorNote, Mono, Skeleton } from "../components/ui";
import { formatDateTime, formatDuration, timeAgo } from "../lib/format";
import { HostStatusBadge } from "./HostsPage";

const inputClass = "h-9 w-full rounded-lg border border-line bg-canvas px-3 text-sm outline-none focus:border-accent";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium tracking-wide text-muted uppercase">{label}</dt>
      <dd className="mt-1 truncate text-sm">{children}</dd>
    </div>
  );
}

function SettingsCard({ host }: { host: RemoteHost }) {
  const update = useUpdateHost(host.id);
  const { toast } = useToast();
  const [name, setName] = useState(host.name);
  const [interval, setInterval] = useState(host.expected_interval_seconds);
  const [subnet, setSubnet] = useState(host.scan_subnet);
  const dirty = name !== host.name || interval !== host.expected_interval_seconds || subnet !== host.scan_subnet;

  function save(event: FormEvent) {
    event.preventDefault();
    update.mutate(
      { name: name.trim(), expected_interval_seconds: interval, scan_subnet: subnet.trim() },
      { onSuccess: () => toast("Host updated"), onError: (e) => toast(e.message, "error") },
    );
  }

  function toggle(changes: Partial<RemoteHost>, message: string) {
    update.mutate(changes, { onSuccess: () => toast(message), onError: (e) => toast(e.message, "error") });
  }

  return (
    <Card className="p-5">
      <h2 className="font-semibold">Settings</h2>
      <form onSubmit={save} className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block space-y-1.5">
          <span className="text-xs font-medium tracking-wide text-muted uppercase">Name</span>
          <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
        </label>
        <label className="block space-y-1.5">
          <span className="text-xs font-medium tracking-wide text-muted uppercase">Check-in interval (s)</span>
          <input
            type="number"
            min={10}
            max={86400}
            value={interval}
            onChange={(e) => setInterval(Number(e.target.value))}
            className={inputClass}
          />
        </label>
        {host.is_scanner && (
          <label className="block space-y-1.5 sm:col-span-2">
            <span className="text-xs font-medium tracking-wide text-muted uppercase">Scan subnet</span>
            <input
              value={subnet}
              onChange={(e) => setSubnet(e.target.value)}
              placeholder="Empty = server default (SCAN_SUBNET) or auto-detect"
              className={cx(inputClass, "font-mono")}
            />
          </label>
        )}
        {dirty && (
          <div className="flex justify-end gap-2 sm:col-span-2">
            <Button type="button" size="sm" variant="ghost" onClick={() => {
              setName(host.name);
              setInterval(host.expected_interval_seconds);
              setSubnet(host.scan_subnet);
            }}>
              Discard
            </Button>
            <Button type="submit" size="sm" variant="primary" loading={update.isPending}>
              Save
            </Button>
          </div>
        )}
      </form>
      <div className="mt-5 space-y-3 border-t border-line pt-4">
        <label className="flex items-center justify-between gap-4 text-sm">
          <span>
            <span className="font-medium">LAN scanner</span>
            <span className="block text-muted">Allow this host to report ARP scans.</span>
          </span>
          <input
            type="checkbox"
            className="size-4"
            checked={host.is_scanner}
            onChange={(e) => toggle({ is_scanner: e.target.checked }, e.target.checked ? "Scanner enabled" : "Scanner disabled")}
          />
        </label>
        <label className="flex items-center justify-between gap-4 text-sm">
          <span>
            <span className="font-medium">Active</span>
            <span className="block text-muted">Disabling rejects its token and stops offline alerts.</span>
          </span>
          <input
            type="checkbox"
            className="size-4"
            checked={host.is_active}
            onChange={(e) => toggle({ is_active: e.target.checked }, e.target.checked ? "Host enabled" : "Host disabled")}
          />
        </label>
      </div>
    </Card>
  );
}

const checkTone = { up: "ok", down: "danger", unknown: "neutral" } as const;

function CheckRow({ check }: { check: HostCheck }) {
  const update = useUpdateCheck();
  const remove = useDeleteCheck();
  const { toast } = useToast();
  return (
    <motion.li
      layout="position"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, height: 0 }}
      className="overflow-hidden"
    >
      <div className={cx("flex items-center gap-3 px-5 py-3", !check.enabled && "opacity-50")}>
        <Badge tone={checkTone[check.status]}>{check.status}</Badge>
        <div className="min-w-0 flex-1">
          <Mono className="block truncate">{check.description}</Mono>
          <p className="truncate text-xs text-muted">
            every {check.interval_seconds}s
            {check.last_checked_at && ` · ${timeAgo(check.last_checked_at)}`}
            {check.last_latency_ms !== null && check.status === "up" && ` · ${Math.round(check.last_latency_ms)} ms`}
            {check.last_error && check.status !== "up" && ` · ${check.last_error}`}
          </p>
        </div>
        <input
          type="checkbox"
          className="size-4"
          aria-label={`Enable ${check.description}`}
          checked={check.enabled}
          onChange={(e) => update.mutate({ id: check.id, enabled: e.target.checked })}
        />
        <Button
          size="sm"
          variant="ghost"
          aria-label={`Delete ${check.description}`}
          loading={remove.isPending}
          onClick={() => remove.mutate(check.id, { onSuccess: () => toast("Check removed") })}
        >
          <TrashIcon size={14} />
        </Button>
      </div>
    </motion.li>
  );
}

function ChecksCard({ host }: { host: RemoteHost }) {
  const create = useCreateCheck();
  const { toast } = useToast();
  const [kind, setKind] = useState<CheckKind>("ping");
  const [target, setTarget] = useState(host.last_ip ?? "");
  const [port, setPort] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(
      { host: host.id, kind, target: target.trim(), port: kind === "tcp" ? Number(port) : null },
      {
        onSuccess: () => {
          toast("Check added");
          setPort("");
        },
      },
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-line px-5 py-4">
        <h2 className="font-semibold">Checks</h2>
        <p className="text-sm text-muted">Probes run from the server. Useful for hosts that can’t run the agent.</p>
      </div>
      {host.checks.length === 0 ? (
        <p className="px-5 py-4 text-sm text-muted">No checks yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          <AnimatePresence initial={false}>
            {host.checks.map((check) => (
              <CheckRow key={check.id} check={check} />
            ))}
          </AnimatePresence>
        </ul>
      )}
      <form onSubmit={submit} className="flex flex-col gap-2 border-t border-line bg-raised/40 p-4 sm:flex-row">
        <select
          aria-label="Check type"
          value={kind}
          onChange={(e) => setKind(e.target.value as CheckKind)}
          className={cx(inputClass, "sm:w-28")}
        >
          <option value="ping">Ping</option>
          <option value="tcp">TCP</option>
          <option value="http">HTTP</option>
        </select>
        <input
          required
          aria-label="Target"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder={kind === "http" ? "https://nas.lan:5001/" : "hostname or IP"}
          className={cx(inputClass, "flex-1")}
        />
        {kind === "tcp" && (
          <input
            required
            aria-label="Port"
            type="number"
            min={1}
            max={65535}
            value={port}
            onChange={(e) => setPort(e.target.value)}
            placeholder="port"
            className={cx(inputClass, "sm:w-24")}
          />
        )}
        <Button type="submit" size="sm" variant="secondary" className="h-9" loading={create.isPending}>
          <PlusIcon size={14} /> Add
        </Button>
      </form>
      {create.error && (
        <div className="px-4 pb-4">
          <ErrorNote error={create.error} />
        </div>
      )}
    </Card>
  );
}

const eventDot: Record<HostEvent["event_type"], string> = {
  online: "bg-ok",
  offline: "bg-danger",
  check_failed: "bg-warn",
  check_recovered: "bg-ok",
};

function eventSummary(event: HostEvent): string {
  const d = event.details as Record<string, string | number | null | undefined>;
  if (event.event_type === "online" && typeof d.down_seconds === "number") return `after ${formatDuration(d.down_seconds)} down`;
  if (event.event_type.startsWith("check_")) return [d.check, d.error].filter(Boolean).join(": ");
  return "";
}

function Timeline({ hostId }: { hostId: number }) {
  const { data, isPending } = useHostEvents(hostId);
  if (isPending) return <Skeleton className="m-5 h-24" />;
  if (!data?.length) return <EmptyState title="No events yet" />;
  return (
    <ol className="relative px-5 py-4">
      <span className="absolute top-6 bottom-6 left-[1.6rem] w-px bg-line" aria-hidden="true" />
      {data.map((event) => (
        <li key={event.id} className="relative flex gap-4 py-2">
          <span className={cx("relative z-10 mt-1.5 size-2.5 shrink-0 rounded-full ring-4 ring-surface", eventDot[event.event_type])} />
          <div className="min-w-0 flex-1 text-sm">
            <span className="font-medium">{event.event_type_display}</span>
            <span className="ml-2 break-words text-muted">{eventSummary(event)}</span>
          </div>
          <time className="shrink-0 text-xs text-muted tabular-nums" dateTime={event.timestamp} title={formatDateTime(event.timestamp)}>
            {timeAgo(event.timestamp)}
          </time>
        </li>
      ))}
    </ol>
  );
}

export function HostDetailPage() {
  const id = Number(useParams().id);
  const { data: host, isPending, error } = useHost(id);
  const alerts = useAlerts({ remote_host: id });
  const rotate = useRotateHostToken();
  const remove = useDeleteHost();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [newToken, setNewToken] = useState<string | null>(null);

  const back = (
    <Link to="/hosts" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
      <ArrowLeftIcon size={16} /> All hosts
    </Link>
  );

  if (isPending) {
    return (
      <div className="space-y-4">
        {back}
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-48 rounded-2xl" />
      </div>
    );
  }
  if (error || !host) {
    return (
      <div className="space-y-4">
        {back}
        <EmptyState title={error instanceof ApiError && error.status === 404 ? "Host not found" : "Could not load host"}>
          {error?.message}
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {back}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-2">
          <h1 className="truncate text-2xl font-semibold tracking-tight">{host.name}</h1>
          <div className="flex flex-wrap gap-2">
            <HostStatusBadge host={host} />
            {host.is_scanner && <Badge tone="accent">LAN scanner</Badge>}
            {host.agent_version && <Badge>agent {host.agent_version}</Badge>}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" onClick={() => setConfirmRotate(true)}>
            <RefreshIcon size={16} /> New token
          </Button>
          <Button variant="secondary" onClick={() => setConfirmDelete(true)} aria-label="Delete host">
            <TrashIcon size={16} />
          </Button>
        </div>
      </div>

      <Card className="p-5">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
          <Field label="Last check-in">
            <span title={formatDateTime(host.last_checkin)}>{timeAgo(host.last_checkin)}</span>
          </Field>
          <Field label="Hostname">{host.reported_hostname || "—"}</Field>
          <Field label="Address">
            <Mono>{host.last_ip ?? "—"}</Mono>
          </Field>
          <Field label="Uptime">{formatDuration(host.uptime_seconds)}</Field>
        </dl>
        {(host.cpu_percent !== null || host.mem_percent !== null || host.disk_percent !== null) && (
          <div className="mt-5 grid grid-cols-3 gap-4 border-t border-line pt-5">
            <MetricBar label="CPU" value={host.cpu_percent} />
            <MetricBar label="Memory" value={host.mem_percent} />
            <MetricBar label="Disk" value={host.disk_percent} />
          </div>
        )}
        {host.status === "unknown" && (
          <p className="mt-5 rounded-lg bg-info/10 px-3 py-2 text-sm text-info">
            Waiting for the first check-in. Lost the token? Use <strong>New token</strong> to get setup instructions again.
          </p>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          <ChecksCard key={host.id} host={host} />
          <SettingsCard key={`${host.id}-${host.name}-${host.expected_interval_seconds}-${host.scan_subnet}`} host={host} />
          <Card className="overflow-hidden">
            <h2 className="border-b border-line px-5 py-4 font-semibold">Alerts</h2>
            <AlertList alerts={alerts.data?.results ?? []} emptyText="No alerts for this host" />
          </Card>
        </div>
        <Card className="self-start overflow-hidden lg:col-span-2">
          <h2 className="border-b border-line px-5 py-4 font-semibold">History</h2>
          <Timeline hostId={host.id} />
        </Card>
      </div>

      <ConfirmDialog
        open={confirmRotate}
        onClose={() => setConfirmRotate(false)}
        title="Issue a new token?"
        description="The current token stops working immediately. Update the agent's config with the new one."
        confirmLabel="Issue new token"
        loading={rotate.isPending}
        onConfirm={() =>
          rotate.mutate(host.id, {
            onSuccess: ({ token }) => {
              setConfirmRotate(false);
              setNewToken(token);
            },
            onError: (e) => toast(e.message, "error"),
          })
        }
      />
      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete this host?"
        description={`"${host.name}", its checks, events and alerts are deleted, and its token stops working.`}
        confirmLabel="Delete host"
        danger
        loading={remove.isPending}
        onConfirm={() =>
          remove.mutate(host.id, {
            onSuccess: () => {
              toast("Host deleted");
              navigate("/hosts", { replace: true });
            },
            onError: (e) => toast(e.message, "error"),
          })
        }
      />
      <SecretDialog secret={newToken} title={`New token for "${host.name}"`} onClose={() => setNewToken(null)}>
        {newToken && <HostSetup host={host} token={newToken} />}
      </SecretDialog>
    </div>
  );
}
