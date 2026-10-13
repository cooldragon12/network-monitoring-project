import { useEffect, useId, useRef, type ReactNode } from "react";

import { Button } from "./ui";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
}

const supportsClosedBy = typeof HTMLDialogElement !== "undefined" && "closedBy" in HTMLDialogElement.prototype;

/**
 * Native modal <dialog>: top layer, focus trapping and Esc handling come from
 * the browser. Light dismiss uses closedby="any", with a backdrop-click
 * fallback for browsers (Safari) that lack it.
 */
export function Dialog({ open, onClose, title, description, children, actions }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function handleClick(event: React.MouseEvent<HTMLDialogElement>) {
    if (supportsClosedBy) return;
    const dialog = ref.current;
    if (!dialog || event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    const inside =
      rect.top <= event.clientY &&
      event.clientY <= rect.bottom &&
      rect.left <= event.clientX &&
      event.clientX <= rect.right;
    if (!inside) dialog.close();
  }

  return (
    <dialog
      ref={ref}
      {...{ closedby: "any" }}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={handleClick}
      className="app-dialog m-auto w-[min(28rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-fg shadow-2xl"
    >
      <div className="p-6">
        <h2 id={titleId} className="text-lg font-semibold">
          {title}
        </h2>
        {description && <div className="mt-1.5 text-sm text-muted">{description}</div>}
        {children && <div className="mt-4">{children}</div>}
      </div>
      {actions && <div className="flex justify-end gap-2 border-t border-line bg-raised/50 px-6 py-4">{actions}</div>}
    </dialog>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  loading?: boolean;
}

export function ConfirmDialog({ open, onClose, onConfirm, title, description, confirmLabel, danger, loading }: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} loading={loading} autoFocus>
            {confirmLabel}
          </Button>
        </>
      }
    />
  );
}
