import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { useScanNow, useStats } from "../api/hooks";
import { RadarIcon } from "./icons";
import { useToast } from "./Toasts";
import { Button } from "./ui";

const FAST_POLL_MS = 2_000;
// Matches the server-side expiry of a queued scan (devices.tasks.SCAN_REQUEST_TTL).
const WORKER_TIMEOUT_MS = 120_000;

interface Pending {
  afterId: number | null;
  since: number;
}

/**
 * Queues a scan for the scanner worker and follows it to completion by polling
 * /api/stats/ until a newer, finished ScanRun appears.
 */
export function ScanButton() {
  const [pending, setPending] = useState<Pending | null>(null);
  const { data: stats } = useStats(pending ? FAST_POLL_MS : undefined);
  const scanNow = useScanNow();
  const qc = useQueryClient();
  const { toast } = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;

  // A scan queued elsewhere (another tab, or before a reload) is followed too.
  useEffect(() => {
    if (!pending && stats?.scan_requested) setPending({ afterId: stats.last_scan?.id ?? null, since: Date.now() });
  }, [pending, stats?.scan_requested, stats?.last_scan?.id]);

  useEffect(() => {
    if (!pending || !stats) return;
    const scan = stats.last_scan;
    const isNew = scan && scan.id !== pending.afterId;
    if (isNew && scan.status !== "running") {
      setPending(null);
      void qc.invalidateQueries({ queryKey: ["devices"] });
      void qc.invalidateQueries({ queryKey: ["alerts"] });
      if (scan.status === "success") toastRef.current(`Scan finished: ${scan.devices_found} devices answered`);
      else toastRef.current(`Scan failed: ${scan.error}`, "error");
    } else if (!isNew && !stats.scan_requested && Date.now() - pending.since > FAST_POLL_MS * 3) {
      // Flag gone without a new scan: the queued request expired unprocessed.
      setPending(null);
      toastRef.current("The scanner worker didn't pick up the scan. Is it running? (sudo scripts/run_scanner_worker.sh)", "error");
    } else if (Date.now() - pending.since > WORKER_TIMEOUT_MS) {
      setPending(null);
      toastRef.current("No response from the scanner worker. Is it running?", "error");
    }
  }, [pending, stats, qc]);

  const running = pending && stats?.last_scan?.id !== pending.afterId && stats?.last_scan?.status === "running";
  const label = running ? "Scanning…" : pending ? "Queued…" : "Scan now";

  return (
    <Button
      size="sm"
      variant="secondary"
      className="shrink-0 self-start whitespace-nowrap sm:self-auto"
      disabled={!!pending}
      loading={scanNow.isPending}
      aria-live="polite"
      onClick={() =>
        scanNow.mutate(undefined, {
          onSuccess: (res) => setPending({ afterId: res.after_scan_id, since: Date.now() }),
          onError: (err) => {
            if (err instanceof ApiError && err.status === 409) {
              toast("A scan is already queued or running", "info");
              setPending({ afterId: stats?.last_scan?.id ?? null, since: Date.now() });
            } else {
              toast(err.message, "error");
            }
          },
        })
      }
    >
      <RadarIcon size={15} className={pending ? "animate-spin [animation-duration:2s] motion-reduce:animate-none" : undefined} />
      {label}
    </Button>
  );
}
