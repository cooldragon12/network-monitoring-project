import type { RemoteHost } from "../api/types";
import { CodeBlock } from "./SecretDialog";

/** Copy-paste setup for a freshly created/rotated host token. */
export function HostSetup({ host, token }: { host: Pick<RemoteHost, "is_scanner" | "name">; token: string }) {
  const server = window.location.origin;
  if (host.is_scanner) {
    return (
      <div className="mt-4 space-y-3 text-sm">
        <p className="font-medium">Run the scanner agent on a machine on your LAN</p>
        <p className="text-muted">
          This PC: put this in <code className="font-mono">scanner.env</code> in the project folder, then run{" "}
          <code className="font-mono">sudo scripts/run_scanner.sh</code>.
        </p>
        <CodeBlock code={`NETMON_SERVER_URL=${server}\nNETMON_TOKEN=${token}`} />
        <p className="text-muted">Raspberry Pi / Linux NAS with Docker (about 25 MB of RAM):</p>
        <CodeBlock
          code={`docker run -d --name netmon-scanner --restart unless-stopped \\\n  --network host --cap-drop ALL --cap-add NET_RAW \\\n  -e NETMON_SERVER_URL=${server} \\\n  -e NETMON_TOKEN=${token} \\\n  netmon-scanner`}
        />
      </div>
    );
  }
  return (
    <div className="mt-4 space-y-3 text-sm">
      <p className="font-medium">Run the host agent on “{host.name}”</p>
      <p className="text-muted">
        Copy <code className="font-mono">agent/agent.py</code> to the machine (Python 3.9+, no packages needed), then:
      </p>
      <CodeBlock code={`NETMON_SERVER_URL=${server} \\\nNETMON_TOKEN=${token} \\\npython3 agent.py`} />
      <p className="text-muted">
        For a permanent install use <code className="font-mono">agent/netmon-agent.service</code> (systemd).
      </p>
    </div>
  );
}
