import { AnimatePresence, motion } from "motion/react";
import { Link } from "react-router";

import { useAcknowledgeAlert } from "../api/hooks";
import type { Alert, Severity } from "../api/types";
import { formatDateTime, timeAgo } from "../lib/format";
import { BellIcon, CheckIcon } from "./icons";
import { Badge, Button, cx, EmptyState } from "./ui";
import { useToast } from "./Toasts";

const severityBar: Record<Severity, string> = {
  info: "bg-info",
  warning: "bg-warn",
  critical: "bg-danger",
};

const severityTone = { info: "info", warning: "warn", critical: "danger" } as const;

function AlertRow({ alert, compact }: { alert: Alert; compact?: boolean }) {
  const ack = useAcknowledgeAlert();
  const { toast } = useToast();

  const subject = alert.device ? (
    <Link to={`/devices/${alert.device}`} className="font-medium hover:text-accent">
      {alert.subject_name}
    </Link>
  ) : alert.remote_host ? (
    <Link to={`/hosts/${alert.remote_host}`} className="font-medium hover:text-accent">
      {alert.subject_name}
    </Link>
  ) : (
    <span className="font-medium">{alert.subject_name}</span>
  );

  return (
    <motion.li
      layout="position"
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0, transition: { duration: 0.2 } }}
      transition={{ duration: 0.2 }}
      className="overflow-hidden"
    >
      <div className={cx("relative flex gap-3 py-3 pr-4 pl-5", alert.acknowledged && "opacity-60")}>
        <span className={cx("absolute top-3 bottom-3 left-0 w-1 rounded-r-full", severityBar[alert.severity])} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            {subject}
            <Badge tone={severityTone[alert.severity]}>{alert.kind_display}</Badge>
            {alert.suppressed && <Badge>not sent (cooldown)</Badge>}
          </div>
          {!compact && <p className="mt-1 text-sm break-words text-muted">{alert.message}</p>}
          <p className="mt-1 text-xs text-muted" title={formatDateTime(alert.created_at)}>
            {timeAgo(alert.created_at)}
            {alert.acknowledged && " · acknowledged"}
          </p>
        </div>
        {!alert.acknowledged && (
          <Button
            size="sm"
            variant="ghost"
            className="self-start"
            loading={ack.isPending}
            onClick={() =>
              ack.mutate(alert.id, {
                onSuccess: () => toast("Alert acknowledged"),
                onError: (e) => toast(e.message, "error"),
              })
            }
            aria-label={`Acknowledge alert for ${alert.subject_name}`}
          >
            <CheckIcon size={14} />
            <span className="hidden sm:inline">Acknowledge</span>
          </Button>
        )}
      </div>
    </motion.li>
  );
}

export function AlertList({ alerts, compact, emptyText = "No alerts" }: { alerts: Alert[]; compact?: boolean; emptyText?: string }) {
  if (alerts.length === 0) {
    return <EmptyState icon={<BellIcon />} title={emptyText} />;
  }
  return (
    <ul className="divide-y divide-line">
      <AnimatePresence initial={false}>
        {alerts.map((alert) => (
          <AlertRow key={alert.id} alert={alert} compact={compact} />
        ))}
      </AnimatePresence>
    </ul>
  );
}
