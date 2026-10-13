import { useState } from "react";

import { useAcknowledgeAll, useAlerts, useStats } from "../api/hooks";
import { AlertList } from "../components/AlertList";
import { ConfirmDialog } from "../components/Dialog";
import { CheckIcon } from "../components/icons";
import { SegmentedControl } from "../components/SegmentedControl";
import { useToast } from "../components/Toasts";
import { Button, Card, cx, ErrorNote, Skeleton } from "../components/ui";
import { pluralize } from "../lib/format";

type View = "open" | "all";

export function AlertsPage() {
  const [view, setView] = useState<View>("open");
  const alerts = useAlerts(view === "open" ? { acknowledged: false } : {});
  const { data: stats } = useStats();
  const ackAll = useAcknowledgeAll();
  const { toast } = useToast();
  const [confirm, setConfirm] = useState(false);
  const open = stats?.open_alerts ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
          <p className="mt-1 text-sm text-muted">New devices on your network and other events that need a look.</p>
        </div>
        <Button variant="secondary" disabled={open === 0} onClick={() => setConfirm(true)}>
          <CheckIcon size={16} /> Acknowledge all
        </Button>
      </div>

      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-line p-4 sm:px-5">
          <SegmentedControl<View>
            label="Alert filter"
            value={view}
            onChange={setView}
            options={[
              { value: "open", label: "Open", count: open },
              { value: "all", label: "All" },
            ]}
          />
          {alerts.data && view === "all" && (
            <span className="text-sm text-muted">{pluralize(alerts.data.count, "alert")}</span>
          )}
        </div>
        <ErrorNote error={alerts.error} />
        {alerts.isPending ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-16" />
            ))}
          </div>
        ) : (
          <div className={cx("transition-opacity", alerts.isPlaceholderData && "opacity-60")}>
            <AlertList
              alerts={alerts.data?.results ?? []}
              emptyText={view === "open" ? "No open alerts: all clear" : "No alerts yet"}
            />
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Acknowledge all alerts?"
        description={`${pluralize(open, "open alert")} will be marked as acknowledged. Devices stay unknown until you mark them as known.`}
        confirmLabel="Acknowledge all"
        loading={ackAll.isPending}
        onConfirm={() =>
          ackAll.mutate(undefined, {
            onSuccess: ({ acknowledged }) => {
              setConfirm(false);
              toast(`Acknowledged ${pluralize(acknowledged, "alert")}`);
            },
            onError: (e) => toast(e.message, "error"),
          })
        }
      />
    </div>
  );
}
