import { motion } from "motion/react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import { useLogin, useSession } from "../api/hooks";
import { LogoIcon } from "../components/icons";
import { Button, ErrorNote } from "../components/ui";

/** Only allow same-site relative paths as post-login redirect targets. */
function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export function LoginPage() {
  const { data: session } = useSession();
  const login = useLogin();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const next = safeNext(params.get("next"));

  if (session?.authenticated) return <Navigate to={next} replace />;

  function submit(event: FormEvent) {
    event.preventDefault();
    login.mutate({ username, password }, { onSuccess: () => navigate(next, { replace: true }) });
  }

  return (
    <div className="grid min-h-dvh place-items-center px-4">
      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 260, damping: 26 }}
        className="w-full max-w-sm"
      >
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <span className="rounded-2xl bg-accent/15 p-3 text-accent">
            <LogoIcon size={28} />
          </span>
          <h1 className="text-xl font-semibold">Network Monitor</h1>
          <p className="text-sm text-muted">Sign in to see your devices.</p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-line bg-surface p-6 shadow-sm">
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Username</span>
            <input
              name="username"
              autoComplete="username"
              required
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="h-10 w-full rounded-lg border border-line bg-canvas px-3 text-sm outline-none focus:border-accent"
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">Password</span>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="h-10 w-full rounded-lg border border-line bg-canvas px-3 text-sm outline-none focus:border-accent"
            />
          </label>
          <ErrorNote error={login.error} />
          <Button type="submit" variant="primary" className="w-full" loading={login.isPending}>
            Sign in
          </Button>
        </form>
      </motion.div>
    </div>
  );
}
