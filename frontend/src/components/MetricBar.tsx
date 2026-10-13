import { motion } from "motion/react";

import { cx } from "./ui";

/** Small labelled usage bar; amber from 75%, red from 90%. */
export function MetricBar({ label, value }: { label: string; value: number | null }) {
  const pct = value === null ? 0 : Math.max(0, Math.min(100, value));
  const tone = value === null ? "bg-muted/40" : pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-warn" : "bg-ok";
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className="font-medium tabular-nums">{value === null ? "—" : `${Math.round(pct)}%`}</span>
      </div>
      <div
        className="mt-1 h-1.5 overflow-hidden rounded-full bg-raised"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value === null ? undefined : Math.round(pct)}
      >
        <motion.div
          className={cx("h-full rounded-full", tone)}
          initial={false}
          animate={{ width: `${pct}%` }}
          transition={{ type: "spring", stiffness: 120, damping: 20 }}
        />
      </div>
    </div>
  );
}
