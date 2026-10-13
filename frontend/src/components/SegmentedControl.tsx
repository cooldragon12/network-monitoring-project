import { motion } from "motion/react";
import { useId } from "react";

import { cx } from "./ui";

interface Option<T extends string> {
  value: T;
  label: string;
  count?: number;
}

interface Props<T extends string> {
  label: string;
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
}

/** Radio-group style filter with a sliding highlight pill. */
export function SegmentedControl<T extends string>({ label, options, value, onChange }: Props<T>) {
  const pillId = useId();
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-xl bg-raised p-1">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={cx(
              "relative rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
              active ? "text-fg" : "text-muted hover:text-fg",
            )}
          >
            {active && (
              <motion.span
                layoutId={pillId}
                className="absolute inset-0 rounded-lg bg-surface shadow-sm"
                transition={{ type: "spring", stiffness: 500, damping: 38 }}
              />
            )}
            <span className="relative flex items-center gap-1.5">
              {option.label}
              {option.count !== undefined && (
                <span className="text-xs tabular-nums text-muted">{option.count}</span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
