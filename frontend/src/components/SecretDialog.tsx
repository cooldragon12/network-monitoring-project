import { useState, type ReactNode } from "react";

import { Dialog } from "./Dialog";
import { CopyIcon } from "./icons";
import { useToast } from "./Toasts";
import { Button, Mono } from "./ui";

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const { toast } = useToast();
  return (
    <Button
      size="sm"
      variant="secondary"
      aria-label={label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast("Copied to clipboard");
        } catch {
          toast("Copy failed: select the text and copy it manually", "error");
        }
      }}
    >
      <CopyIcon size={14} />
    </Button>
  );
}

/** Shows a just-issued secret once, with a copy button and optional extra help. */
export function SecretDialog({
  secret,
  title,
  onClose,
  children,
}: {
  secret: string | null;
  title: string;
  onClose: () => void;
  children?: ReactNode;
}) {
  const [ack, setAck] = useState(false);
  return (
    <Dialog
      open={secret !== null}
      onClose={() => {
        setAck(false);
        onClose();
      }}
      title={title}
      description="Copy it now. It is stored only as a hash and will never be shown again."
      actions={
        <Button variant="primary" onClick={onClose}>
          {ack ? "Done" : "I've saved it"}
        </Button>
      }
    >
      <div className="flex items-center gap-2 rounded-lg border border-line bg-canvas p-2 pl-3" onCopy={() => setAck(true)}>
        <Mono className="min-w-0 flex-1 break-all select-all">{secret}</Mono>
        {secret && <CopyButton text={secret} label="Copy token" />}
      </div>
      {children}
    </Dialog>
  );
}

export function CodeBlock({ code }: { code: string }) {
  return (
    <div className="relative mt-2">
      <pre className="overflow-x-auto rounded-lg border border-line bg-canvas p-3 pr-12 text-xs leading-relaxed">
        <code className="font-mono">{code}</code>
      </pre>
      <div className="absolute top-1.5 right-1.5">
        <CopyButton text={code} label="Copy snippet" />
      </div>
    </div>
  );
}
