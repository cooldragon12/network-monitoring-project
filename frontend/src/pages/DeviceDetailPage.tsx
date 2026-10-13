import { motion } from "motion/react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError } from "../api/client";
import {
  useAlerts,
  useDeleteDevice,
  useDevice,
  useDeviceEvents,
  useMeta,
  useUpdateDevice,
} from "../api/hooks";
import type { Device, DeviceEvent, DeviceType } from "../api/types";
import { AlertList } from "../components/AlertList";
import { ConfirmDialog } from "../components/Dialog";
import { ArrowLeftIcon, CheckIcon, PencilIcon, ShuffleIcon, TrashIcon } from "../components/icons";
import { useToast } from "../components/Toasts";
import { Badge, Button, Card, cx, EmptyState, ErrorNote, Mono, Skeleton, StatusDot } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/format";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium tracking-wide text-muted uppercase">{label}</dt>
      <dd className="mt-1 truncate text-sm">{children}</dd>
    </div>
  );
}

function LabelEditor({ device }: { device: Device }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(device.label);
  const update = useUpdateDevice(device.id);
  const { toast } = useToast();

  function save(event: FormEvent) {
    event.preventDefault();
    update.mutate(
      { label: value.trim() },
      {
        onSuccess: () => {
          setEditing(false);
          toast("Name saved");
        },
        onError: (e) => toast(e.message, "error"),
      },
    );
  }

  if (!editing) {
    return (
      <div className="flex min-w-0 items-center gap-2">
        <h1 className="truncate text-2xl font-semibold tracking-tight">{device.display_name}</h1>
        <button
          type="button"
          onClick={() => {
            setValue(device.label);
            setEditing(true);
          }}
          className="rounded-lg p-1.5 text-muted hover:bg-raised hover:text-fg"
          aria-label="Rename device"
        >
          <PencilIcon size={16} />
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={save} className="flex w-full max-w-md items-center gap-2">
      <input
        autoFocus
        value={value}
        maxLength={100}
        placeholder={device.hostname || device.vendor || "Friendly name"}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
        aria-label="Device name"
        className="h-10 min-w-0 flex-1 rounded-lg border border-accent bg-canvas px-3 text-lg font-semibold outline-none"
      />
      <Button type="submit" variant="primary" loading={update.isPending}>
        Save
      </Button>
      <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
        Cancel
      </Button>
    </form>
  );
}

function eventSummary(event: DeviceEvent): string {
  const d = event.details as Record<string, string | null | undefined>;
  switch (event.event_type) {
    case "ip_changed":
      return `${d.old_ip} → ${d.new_ip}`;
    case "new_device":
      return [d.ip, d.vendor].filter(Boolean).join(" · ");
    case "online":
      return d.ip ? `at ${d.ip}` : "";
    case "offline":
      return d.last_seen ? `last seen ${formatDateTime(d.last_seen)}` : "";
    default:
      return "";
  }
}

const eventDot: Record<DeviceEvent["event_type"], string> = {
  new_device: "bg-warn",
  first_seen: "bg-info",
  online: "bg-ok",
  offline: "bg-muted",
  ip_changed: "bg-info",
};

function Timeline({ deviceId }: { deviceId: number }) {
  const { data, isPending, error } = useDeviceEvents(deviceId);
  if (isPending) return <Skeleton className="m-5 h-24" />;
  if (error) return <ErrorNote error={error} />;
  const events = data?.results ?? [];
  if (events.length === 0) return <EmptyState title="No events yet" />;
  return (
    <ol className="relative px-5 py-4">
      <span className="absolute top-6 bottom-6 left-[1.6rem] w-px bg-line" aria-hidden="true" />
      {events.map((event, i) => (
        <motion.li
          key={event.id}
          initial={{ opacity: 0, x: -6 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: Math.min(i, 10) * 0.03 }}
          className="relative flex gap-4 py-2"
        >
          <span className={cx("relative z-10 mt-1.5 size-2.5 shrink-0 rounded-full ring-4 ring-surface", eventDot[event.event_type])} />
          <div className="min-w-0 flex-1 text-sm">
            <span className="font-medium">{event.event_type_display}</span>
            <span className="ml-2 text-muted">{eventSummary(event)}</span>
          </div>
          <time className="shrink-0 text-xs text-muted tabular-nums" dateTime={event.timestamp} title={formatDateTime(event.timestamp)}>
            {timeAgo(event.timestamp)}
          </time>
        </motion.li>
      ))}
    </ol>
  );
}

function DetailsCard({ device }: { device: Device }) {
  const { data: meta } = useMeta();
  const update = useUpdateDevice(device.id);
  const { toast } = useToast();
  const [notes, setNotes] = useState(device.notes);
  const notesDirty = notes !== device.notes;

  return (
    <Card className="p-5">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
        <Field label="IP address">
          <Mono>{device.ip ?? "—"}</Mono>
        </Field>
        <Field label="MAC address">
          <Mono>{device.mac}</Mono>
        </Field>
        <Field label="Vendor">{device.vendor || (device.is_random_mac ? "Private address" : "—")}</Field>
        <Field label="Hostname">{device.hostname || "—"}</Field>
        <Field label="First seen">
          <span title={formatDateTime(device.first_seen)}>{timeAgo(device.first_seen)}</span>
        </Field>
        <Field label="Last seen">
          <span title={formatDateTime(device.last_seen)}>{device.is_online ? "now" : timeAgo(device.last_seen)}</span>
        </Field>
      </dl>

      {device.is_random_mac && (
        <p className="mt-5 flex gap-2 rounded-lg bg-info/10 px-3 py-2 text-sm text-info">
          <ShuffleIcon size={16} className="mt-0.5 shrink-0" />
          This device uses a randomized (private) MAC address, typical of phones and tablets. It may show up as a new
          device again if the address rotates.
        </p>
      )}

      <div className="mt-5 grid gap-4 border-t border-line pt-5 sm:grid-cols-[12rem_1fr]">
        <label className="block space-y-1.5">
          <span className="text-xs font-medium tracking-wide text-muted uppercase">Type</span>
          <select
            value={device.device_type}
            disabled={update.isPending}
            onChange={(e) =>
              update.mutate(
                { device_type: e.target.value as DeviceType },
                { onSuccess: () => toast("Type updated"), onError: (err) => toast(err.message, "error") },
              )
            }
            className="h-9 w-full rounded-lg border border-line bg-canvas px-2 text-sm outline-none focus:border-accent"
          >
            {(meta?.device_types ?? [{ value: device.device_type, label: device.device_type_display }]).map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1.5">
          <span className="text-xs font-medium tracking-wide text-muted uppercase">Notes</span>
          <textarea
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Where it lives, who owns it…"
            className="w-full resize-y rounded-lg border border-line bg-canvas px-3 py-2 text-sm outline-none focus:border-accent"
          />
          {notesDirty && (
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setNotes(device.notes)}>
                Discard
              </Button>
              <Button
                size="sm"
                variant="primary"
                loading={update.isPending}
                onClick={() =>
                  update.mutate(
                    { notes },
                    { onSuccess: () => toast("Notes saved"), onError: (err) => toast(err.message, "error") },
                  )
                }
              >
                Save notes
              </Button>
            </div>
          )}
        </label>
      </div>
    </Card>
  );
}

export function DeviceDetailPage() {
  const id = Number(useParams().id);
  const { data: device, isPending, error } = useDevice(id);
  const alerts = useAlerts({ device: id });
  const update = useUpdateDevice(id);
  const remove = useDeleteDevice();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const back = (
    <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
      <ArrowLeftIcon size={16} /> All devices
    </Link>
  );

  if (isPending) {
    return (
      <div className="space-y-4">
        {back}
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-48 rounded-2xl" />
      </div>
    );
  }
  if (error || !device) {
    return (
      <div className="space-y-4">
        {back}
        <EmptyState title={error instanceof ApiError && error.status === 404 ? "Device not found" : "Could not load device"}>
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
          <LabelEditor key={device.label} device={device} />
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={device.is_online ? "ok" : "neutral"}>
              <StatusDot online={device.is_online} className="scale-75" />
              {device.is_online ? "Online" : "Offline"}
            </Badge>
            {device.is_known ? <Badge tone="accent">Known</Badge> : <Badge tone="warn">Unknown</Badge>}
            {device.device_type !== "unknown" && <Badge>{device.device_type_display}</Badge>}
            {device.source === "agent" && <Badge tone="info">Agent</Badge>}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          {!device.is_known && (
            <Button
              variant="primary"
              loading={update.isPending}
              onClick={() =>
                update.mutate(
                  { is_known: true },
                  { onSuccess: () => toast("Marked as known"), onError: (e) => toast(e.message, "error") },
                )
              }
            >
              <CheckIcon size={16} /> Mark as known
            </Button>
          )}
          <Button variant="secondary" onClick={() => setConfirmDelete(true)} aria-label="Forget device">
            <TrashIcon size={16} />
            <span className="hidden sm:inline">Forget</span>
          </Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          <DetailsCard key={device.id} device={device} />
          <Card className="overflow-hidden">
            <h2 className="border-b border-line px-5 py-4 font-semibold">Alerts</h2>
            <AlertList alerts={alerts.data?.results ?? []} emptyText="No alerts for this device" />
          </Card>
        </div>
        <Card className="self-start overflow-hidden lg:col-span-2">
          <h2 className="border-b border-line px-5 py-4 font-semibold">History</h2>
          <Timeline deviceId={device.id} />
        </Card>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Forget this device?"
        description={
          <>
            <strong className="text-fg">{device.display_name}</strong> and its history will be deleted. If it shows up on
            the network again it will be treated as a new, unknown device.
          </>
        }
        confirmLabel="Forget device"
        danger
        loading={remove.isPending}
        onConfirm={() =>
          remove.mutate(device.id, {
            onSuccess: () => {
              toast("Device forgotten");
              navigate("/", { replace: true });
            },
            onError: (e) => toast(e.message, "error"),
          })
        }
      />
    </div>
  );
}
