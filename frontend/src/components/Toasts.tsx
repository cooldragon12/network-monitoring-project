import { AnimatePresence, motion } from "motion/react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CheckIcon, AlertTriangleIcon, XIcon } from "./icons";
import { cx } from "./ui";

type ToastTone = "success" | "error" | "info";

interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastApi {
  toast: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const DISMISS_MS = 4500;
const supportsPopover = typeof HTMLElement !== "undefined" && "popover" in HTMLElement.prototype;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const regionRef = useRef<HTMLDivElement>(null);

  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);

  const toast = useCallback(
    (message: string, tone: ToastTone = "success") => {
      const id = nextId.current++;
      setToasts((all) => [...all.slice(-3), { id, message, tone }]);
      window.setTimeout(() => dismiss(id), DISMISS_MS);
    },
    [dismiss],
  );

  // popover="manual": stays open, no light dismiss. Re-showing moves the region
  // to the top of the top layer so toasts appear above any open modal dialog.
  useEffect(() => {
    const region = regionRef.current;
    if (!region || !supportsPopover) return;
    if (region.matches(":popover-open")) region.hidePopover();
    region.showPopover();
  }, [toasts.length]);

  const value = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div ref={regionRef} popover="manual" className="toast-region" role="status" aria-live="polite">
        <ol className="flex flex-col gap-2">
          <AnimatePresence initial={false}>
            {toasts.map((t) => (
              <motion.li
                key={t.id}
                layout
                initial={{ opacity: 0, y: 16, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, x: 40, transition: { duration: 0.18 } }}
                transition={{ type: "spring", stiffness: 420, damping: 32 }}
                className="flex items-start gap-3 rounded-xl border border-line bg-surface p-3 pr-2 text-sm text-fg shadow-lg"
              >
                <span
                  className={cx(
                    "mt-0.5 rounded-full p-1",
                    t.tone === "success" && "bg-ok/15 text-ok",
                    t.tone === "error" && "bg-danger/15 text-danger",
                    t.tone === "info" && "bg-info/15 text-info",
                  )}
                >
                  {t.tone === "error" ? <AlertTriangleIcon size={14} /> : <CheckIcon size={14} />}
                </span>
                <p className="flex-1 py-0.5">{t.message}</p>
                <button
                  type="button"
                  onClick={() => dismiss(t.id)}
                  className="rounded-md p-1 text-muted hover:bg-raised hover:text-fg"
                  aria-label="Dismiss notification"
                >
                  <XIcon size={14} />
                </button>
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
