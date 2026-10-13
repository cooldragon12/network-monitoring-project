import { AnimatePresence, motion } from "motion/react";
import { useState, type FormEvent } from "react";

import { useCreateToken, useDeleteToken, useTokens } from "../api/hooks";
import type { ApiToken, CreatedApiToken } from "../api/types";
import { ConfirmDialog, Dialog } from "../components/Dialog";
import { CopyIcon, KeyIcon, TrashIcon } from "../components/icons";
import { useToast } from "../components/Toasts";
import { Button, Card, EmptyState, ErrorNote, Mono, Skeleton } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/format";

function NewTokenDialog({ token, onClose }: { token: CreatedApiToken | null; onClose: () => void }) {
  const { toast } = useToast();
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.token);
      setCopied(true);
      toast("Token copied to clipboard");
    } catch {
      toast("Copy failed: select the token and copy it manually", "error");
    }
  }

  return (
    <Dialog
      open={token !== null}
      onClose={() => {
        setCopied(false);
        onClose();
      }}
      title={`Token "${token?.name ?? ""}" created`}
      description="Copy it now. For your security it is stored only as a hash and will never be shown again."
      actions={
        <Button variant="primary" onClick={onClose}>
          {copied ? "Done" : "I've saved it"}
        </Button>
      }
    >
      <div className="flex items-center gap-2 rounded-lg border border-line bg-canvas p-2 pl-3">
        <Mono className="min-w-0 flex-1 break-all select-all">{token?.token}</Mono>
        <Button size="sm" variant="secondary" onClick={copy} aria-label="Copy token">
          <CopyIcon size={14} />
        </Button>
      </div>
      <p className="mt-3 text-xs text-muted">
        Use it as <Mono>Authorization: Bearer &lt;token&gt;</Mono>.
      </p>
    </Dialog>
  );
}

export function TokensPage() {
  const tokens = useTokens();
  const create = useCreateToken();
  const remove = useDeleteToken();
  const { toast } = useToast();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<CreatedApiToken | null>(null);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(name.trim(), {
      onSuccess: (token) => {
        setName("");
        setCreated(token);
      },
    });
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">API tokens</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Personal tokens for scripts and integrations such as Home Assistant. They can read and update the same data
          as you, but cannot create other tokens.
        </p>
      </div>

      <Card className="p-5">
        <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="block flex-1 space-y-1.5">
            <span className="text-sm font-medium">New token name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={100}
              required
              placeholder="e.g. home-assistant"
              className="h-10 w-full rounded-lg border border-line bg-canvas px-3 text-sm outline-none focus:border-accent"
            />
          </label>
          <Button type="submit" variant="primary" loading={create.isPending} disabled={!name.trim()}>
            <KeyIcon size={16} /> Create token
          </Button>
        </form>
        <div className="mt-3">
          <ErrorNote error={create.error} />
        </div>
      </Card>

      <Card className="overflow-hidden">
        <ErrorNote error={tokens.error} />
        {tokens.isPending ? (
          <Skeleton className="m-5 h-16" />
        ) : !tokens.data?.length ? (
          <EmptyState icon={<KeyIcon />} title="No tokens yet" />
        ) : (
          <ul className="divide-y divide-line">
            <AnimatePresence initial={false}>
              {tokens.data.map((token) => (
                <motion.li
                  key={token.id}
                  layout="position"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0, height: 0 }}
                  className="flex items-center gap-4 overflow-hidden px-5 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{token.name}</p>
                    <p className="text-xs text-muted">
                      <Mono>{token.token_prefix}…</Mono> · created {timeAgo(token.created_at)} ·{" "}
                      <span title={formatDateTime(token.last_used_at)}>
                        {token.last_used_at ? `last used ${timeAgo(token.last_used_at)}` : "never used"}
                      </span>
                    </p>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => setRevoking(token)} aria-label={`Revoke ${token.name}`}>
                    <TrashIcon size={14} />
                    <span className="hidden sm:inline">Revoke</span>
                  </Button>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </Card>

      <NewTokenDialog token={created} onClose={() => setCreated(null)} />
      <ConfirmDialog
        open={revoking !== null}
        onClose={() => setRevoking(null)}
        title="Revoke token?"
        description={`Anything using "${revoking?.name}" will stop working immediately.`}
        confirmLabel="Revoke"
        danger
        loading={remove.isPending}
        onConfirm={() =>
          revoking &&
          remove.mutate(revoking.id, {
            onSuccess: () => {
              toast(`Revoked "${revoking.name}"`);
              setRevoking(null);
            },
            onError: (e) => toast(e.message, "error"),
          })
        }
      />
    </div>
  );
}
