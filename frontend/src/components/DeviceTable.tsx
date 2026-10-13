import { AnimatePresence, motion } from "motion/react";
import { Link, useNavigate } from "react-router";

import type { Device } from "../api/types";
import { formatDateTime, timeAgo } from "../lib/format";
import { ShuffleIcon } from "./icons";
import { Badge, cx, Mono, StatusDot } from "./ui";

interface Props {
  devices: Device[];
  selected: Set<number>;
  onToggle: (id: number) => void;
  onToggleAll: (ids: number[], select: boolean) => void;
}

const rowMotion = {
  layout: "position" as const,
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.2 },
};

function DeviceName({ device }: { device: Device }) {
  const secondary = device.label ? device.hostname || device.vendor : device.hostname ? device.vendor : "";
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        <Link
          to={`/devices/${device.id}`}
          className="truncate font-medium hover:text-accent"
          onClick={(e) => e.stopPropagation()}
        >
          {device.display_name}
        </Link>
        {!device.is_known && <Badge tone="warn">Unknown</Badge>}
        {device.is_random_mac && (
          <span title="Randomized (private) MAC address" className="text-muted">
            <ShuffleIcon size={14} />
            <span className="sr-only">Randomized MAC</span>
          </span>
        )}
      </div>
      {secondary && <div className="truncate text-xs text-muted">{secondary}</div>}
    </div>
  );
}

function SelectBox({ device, selected, onToggle }: { device: Device; selected: boolean; onToggle: (id: number) => void }) {
  if (device.is_known) return <span className="inline-block size-4" />;
  return (
    <input
      type="checkbox"
      className="size-4 rounded"
      checked={selected}
      onChange={() => onToggle(device.id)}
      onClick={(e) => e.stopPropagation()}
      aria-label={`Select ${device.display_name}`}
    />
  );
}

export function DeviceTable({ devices, selected, onToggle, onToggleAll }: Props) {
  const navigate = useNavigate();
  const selectable = devices.filter((d) => !d.is_known).map((d) => d.id);
  const allSelected = selectable.length > 0 && selectable.every((id) => selected.has(id));

  return (
    // Container queries: the layout follows the card's width, not the viewport,
    // because the card shares the row with the alerts panel on wide screens.
    <div className="@container">
      {/* Wide card: real table */}
      <div className="hidden @xl:block">
        <table className="w-full table-fixed text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs font-medium tracking-wide text-muted uppercase">
              <th className="w-10 py-2.5 pl-5">
                <input
                  type="checkbox"
                  className="size-4 rounded"
                  disabled={selectable.length === 0}
                  checked={allSelected}
                  onChange={() => onToggleAll(selectable, !allSelected)}
                  aria-label="Select all unknown devices"
                />
              </th>
              <th className="w-8 py-2.5" aria-label="Status" />
              <th className="py-2.5">Device</th>
              <th className="w-32 py-2.5">IP</th>
              <th className="hidden w-40 py-2.5 @3xl:table-cell">MAC</th>
              <th className="w-24 py-2.5 pr-5 text-right">Last seen</th>
            </tr>
          </thead>
          <tbody>
            <AnimatePresence initial={false}>
              {devices.map((device) => (
                <motion.tr
                  key={device.id}
                  {...rowMotion}
                  onClick={() => navigate(`/devices/${device.id}`)}
                  className={cx(
                    "cursor-pointer border-b border-line last:border-0 hover:bg-raised/60",
                    !device.is_known && "bg-warn/[0.06]",
                  )}
                >
                  <td className="py-3 pl-5">
                    <SelectBox device={device} selected={selected.has(device.id)} onToggle={onToggle} />
                  </td>
                  <td className="py-3">
                    <StatusDot online={device.is_online} />
                  </td>
                  <td className="py-3 pr-4">
                    <DeviceName device={device} />
                  </td>
                  <td className="py-3">
                    <Mono>{device.ip ?? "—"}</Mono>
                  </td>
                  <td className="hidden py-3 @3xl:table-cell">
                    <Mono className="text-muted">{device.mac}</Mono>
                  </td>
                  <td className="py-3 pr-5 text-right text-muted tabular-nums" title={formatDateTime(device.last_seen)}>
                    {device.is_online ? "now" : timeAgo(device.last_seen)}
                  </td>
                </motion.tr>
              ))}
            </AnimatePresence>
          </tbody>
        </table>
      </div>

      {/* Narrow card (phones): stacked rows */}
      <ul className="divide-y divide-line @xl:hidden">
        <AnimatePresence initial={false}>
          {devices.map((device) => (
            <motion.li
              key={device.id}
              {...rowMotion}
              className={cx("flex items-start gap-3 px-4 py-3", !device.is_known && "bg-warn/[0.06]")}
            >
              <div className="flex items-center gap-3 pt-1">
                <SelectBox device={device} selected={selected.has(device.id)} onToggle={onToggle} />
                <StatusDot online={device.is_online} />
              </div>
              <div className="min-w-0 flex-1">
                <DeviceName device={device} />
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
                  <Mono>{device.ip ?? "—"}</Mono>
                  {device.display_name !== device.mac && <Mono>{device.mac}</Mono>}
                  <span>{device.is_online ? "online" : `seen ${timeAgo(device.last_seen)}`}</span>
                </div>
              </div>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}
