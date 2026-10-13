# Network Device Monitor

Self-hosted monitor for **your own** network: discovers LAN devices via ARP and
alerts on unknown ones, and tracks hosts (this PC, a Pi, a NAS, a VPS) through
lightweight agents.

> Status: **Phase 6**: LAN scanner agent, host agents and server-side checks.

## Architecture

```
 Docker Compose (server)                                Agents (standalone, HTTP only)
 ┌───────────────────────────────────────────┐        ┌─────────────────────────────────┐
 │ web (Django + React)  ◄── /api/agent/* ───┼────────┤ scanner agent: on a LAN machine │
 │ worker: notifications, host checks        │        │   ARP-scans, reports results    │
 │ beat:   offline detection, check schedule │        ├─────────────────────────────────┤
 │ db (Postgres)  redis  ntfy                │◄───────┤ host agent: any Linux/macOS box │
 └───────────────────────────────────────────┘        │   CPU / mem / disk / uptime     │
                                                       └─────────────────────────────────┘
```

* **The scanner agent** (`python -m scanner agent`) runs on any machine on
  the LAN: this PC for now, a Raspberry Pi or NAS later. It never touches the
  database or Redis. It heartbeats the server every 5 s; the server decides
  when a scan is due (every `SCAN_INTERVAL_MINUTES`, or **Scan now**), the agent
  sweeps the subnet and posts the results. Scans never run in the web app.
* **The host agent** (`agent/agent.py`, one file, stdlib only) reports health.
  A host that misses its check-in window goes offline with a critical alert,
  and a scanner is a host too, so you hear about a dead scanner.
* **Checks**: the server can also ping / TCP-connect / HTTP-GET hosts that
  can't run an agent.

Docker Desktop on macOS can't see the LAN at layer 2, so on this PC the scanner
agent runs natively; on a Linux Pi/NAS it can run in its own tiny container.

**Double NAT note:** this network sits behind a Xiaomi router (192.168.31.1),
which itself sits behind the main router (192.168.1.1). ARP does not cross
routers, so devices on 192.168.1.0/24 are invisible from here. To monitor that
segment, add a second scanner on a machine connected to it and set its subnet.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m scanner update-oui   # IEEE vendor DB (a few MB); the server uses it for vendors

cp .env.example .env                     # fill in DJANGO_SECRET_KEY and POSTGRES_PASSWORD
                                         # (and the same password inside DATABASE_URL)
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Dashboard: <http://127.0.0.1:8000/> (or `SITE_URL`). For plain-HTTP local
development set `DJANGO_DEBUG=true` and `DJANGO_SECURE_COOKIES=false` in
`.env`; production defaults are DEBUG off and secure cookies on.

All configuration is via environment variables; see `.env.example`. Native
processes (`manage.py`) read `.env` automatically; real environment variables
take precedence.

## LAN scanner agent

1. Dashboard → **Hosts** → **Add host**, tick **LAN scanner**. Copy the token
   (shown once) and the setup snippet.
2. Run the agent on a machine on the LAN:

**This PC (macOS/Linux, native):**

```bash
cp scanner.env.example scanner.env      # set NETMON_SERVER_URL and NETMON_TOKEN
sudo scripts/run_scanner.sh             # raw sockets need root here
```

**Raspberry Pi / Linux NAS, Docker** (stdlib only, ~25 MB RAM):

```bash
docker build -f scanner/Dockerfile -t netmon-scanner .     # on the Pi, from a checkout
docker run -d --name netmon-scanner --restart unless-stopped \
  --network host --cap-drop ALL --cap-add NET_RAW \
  -e NETMON_SERVER_URL=http://192.168.31.234:8000 -e NETMON_TOKEN=... netmon-scanner
```

If the whole stack runs on a Linux box on the LAN, use
`docker compose --profile scanner up -d` with `NETMON_SCANNER_TOKEN` in `.env`.

**Raspberry Pi / Linux NAS, systemd** (no Docker, unprivileged user with only
`CAP_NET_RAW`, 64 MB memory cap): see `scanner/deploy/netmon-scanner.service`.

| Backend | Platforms | Peak memory (measured) | Needs |
|---|---|---|---|
| `raw` (default on Linux) | Linux | ~25 MB | `CAP_NET_RAW` |
| `scapy` (default on macOS) | macOS, Linux | ~82 MB | root / BPF access |
| `arp-scan` | both | external binary | root |

The server owns the configuration: subnet (per host, else `SCAN_SUBNET`, else
the agent auto-detects), interval, and timeout. Vendors and the random-MAC flag
are worked out server-side, so the agent stays small and isn't trusted for them.

What each successful scan does:

* **Never-seen MAC**: creates the device with `is_known=False`, records a
  `new_device` event and raises an alert. Randomized/private MACs (phones with
  Wi-Fi privacy) get a softer `new_random_mac` alert (severity *info*); normal
  MACs get `new_device` (severity *warning*).
* **Seen device**: marked online, `last_seen` updated; IP changes record an
  `ip_changed` event, and returning devices an `online` event.
* **Missing device**: after `OFFLINE_AFTER_MISSED_SCANS` (default 3)
  consecutive misses it is marked offline with an `offline` event. Only LAN
  devices inside the scanned subnet count.
* **Failed or empty scans** (permission error, interface down) are recorded as
  failed `ScanRun`s and never count as misses, so a broken scanner does not
  mark your whole network offline. A scan whose agent never reports back is
  closed as failed after 5 minutes.

The scanning machine itself never appears in results (it does not ARP-reply to
itself). For debugging without the agent: `sudo .venv/bin/python manage.py scan_now`.

## Hosts and agents

Every machine running an agent is a **host** with its own token (stored only as
a SHA-256 hash, shown once, rotatable, revocable by disabling the host).
Add hosts on the dashboard's **Hosts** page or in the Django admin.

**Host agent** (Linux or macOS, Python 3.9+, no packages, no privileges):

```bash
NETMON_SERVER_URL=http://192.168.31.234:8000 NETMON_TOKEN=... python3 agent/agent.py
python3 agent/agent.py --print           # show what it would send
```

Permanent install: `agent/netmon-agent.service` (systemd, unprivileged, sandboxed)
with `agent/agent.env.example`. It reports hostname, uptime, CPU, memory and
disk every `NETMON_INTERVAL` seconds (default 60), with backoff when the server
is unreachable.

* **Offline detection**: `beat` runs `check_agents` every 30 s. A host with no
  check-in for 2 × its expected interval goes offline: event + critical
  `host_offline` alert (max-priority push). It comes back online on its next
  check-in, and the event records how long it was down.
* **Checks** (per host, on the host page): `ping` (unprivileged ICMP), `tcp`
  (connect to a port), `http` (GET, success below status 400). After
  `failure_threshold` consecutive failures (default 2) a check goes down and
  raises a `check_failed` alert; recovery is recorded in the host history.
* **Rate limiting**: agent endpoints are limited per host
  (`AGENT_THROTTLE_RATE`, default 120/min).

## Remote access (Twingate) and HTTPS

The dashboard, ntfy and agent API are meant to stay on your LAN. To reach them
from outside, **don't port-forward**. Use your Twingate network:

1. Run a Twingate **Connector** on a machine on the LAN (Docker on this PC,
   the Pi or the NAS).
2. Add a **Resource** for this PC's LAN IP (`192.168.31.234`) with ports
   `8000` (dashboard + agent API) and `8080` (ntfy).
3. Phone/laptop with the Twingate client: open `SITE_URL` and get ntfy
   alerts away from home, with notification links working unchanged.
4. Agents outside the LAN (e.g. a VPS): install Twingate's headless Linux
   client with a Service Account, then point `NETMON_SERVER_URL` at the LAN
   address as usual.

Twingate encrypts traffic end to end. If you ever do expose the app publicly,
or want HTTPS on the LAN, put **Caddy** in front: see `deploy/Caddyfile.example`
for the matching `.env` settings (`DJANGO_BEHIND_HTTPS_PROXY`, secure cookies,
`CSRF_TRUSTED_ORIGINS`). Agents accept a private CA via `NETMON_CA_FILE`.

## Dashboard

A React single-page app (TypeScript, Vite, Tailwind CSS v4, Motion) in
`frontend/`, built to static files that Django serves at `/` (same origin, so
the session cookie and CSRF protection just work; no tokens in the browser).

* Device table with Online/Offline/Unknown filters and search; unknown devices
  are highlighted and can be bulk-approved ("Mark as known").
* Device page: rename, set type and notes, mark known, forget, alerts, history.
* **Scan now** asks the online scanner(s) to scan at their next heartbeat
  (≤ 5 s) and follows the scan to a result toast. A banner warns when no
  scanner is set up or the scanner is offline.
* Hosts: status, metrics, checks, history; add hosts (token + setup snippet
  shown once), rotate tokens, set the scan subnet.
* Alerts (open/all, acknowledge one or all) and personal API tokens.
* Follows the system light/dark theme and `prefers-reduced-motion`; works on phones.
* Data refreshes every 15 s (live WebSocket updates are Phase 7).

Every page and API call requires login. For frontend development with hot reload:

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173, proxies /api and /admin to :8000
```

## REST API

All dashboard endpoints live under `/api/` and require a browser session (with
`X-CSRFToken` on unsafe methods) or a personal API token:

```bash
curl -H "Authorization: Bearer $TOKEN" http://192.168.31.234:8000/api/devices/?known=false
```

Personal tokens (dashboard → **API tokens**) are shown once, stored hashed and
revocable; they cannot create tokens or hosts. Agent tokens only work on
`/api/agent/*`, and personal tokens never do.

| Endpoint | Methods | Notes |
|---|---|---|
| `devices/` | GET | `status=online\|offline`, `known=`, `random=`, `search=`, `ordering=` |
| `devices/{id}/` | GET, PATCH, DELETE | PATCH `label`, `device_type`, `notes`, `is_known` (true also acks its new-device alerts) |
| `devices/{id}/events/`, `events/` | GET | event history (`device=`, `event_type=`) |
| `devices/mark-known/` | POST | `{"ids": [1, 2]}` |
| `alerts/` | GET | `acknowledged=`, `kind=`, `severity=`, `device=`, `remote_host=` |
| `alerts/{id}/acknowledge/`, `alerts/acknowledge-all/` | POST | |
| `scans/` | GET | scan runs |
| `scan/` | POST | ask scanners to scan now: `202`; `409` if queued/running; `503` if no scanner online; 6/min |
| `hosts/` | GET, POST, PATCH, DELETE | POST returns the token once (browser session only) |
| `hosts/{id}/rotate-token/`, `hosts/{id}/events/` | POST / GET | rotate: browser session only |
| `checks/` | GET, POST, PATCH, DELETE | `host=` filter |
| `stats/`, `meta/` | GET | dashboard summary (incl. scanners), choice lists |
| `auth/session/`, `auth/login/`, `auth/logout/` | GET/POST | login rate-limited (`LOGIN_THROTTLE_RATE`, 10/min) |
| `tokens/` | GET, POST, DELETE | browser session only |
| `agent/checkin/` | POST | host agent (agent token) |
| `agent/scanner/heartbeat/`, `agent/scanner/scans/`, `agent/scanner/scans/{id}/` | POST | scanner agent (agent token, host must be a scanner) |

## Notifications (self-hosted ntfy)

The Compose stack includes an [ntfy](https://ntfy.sh) server with
`auth-default-access: deny-all`, so nothing can read or publish without an
account. One-time setup (each `user add` prompts for a password):

```bash
docker compose up -d ntfy
# Publisher used by this app: write-only on the topic, authenticates by token
docker compose exec ntfy ntfy user add netmon-publisher
docker compose exec ntfy ntfy access netmon-publisher netmon-alerts write-only
docker compose exec ntfy ntfy token add netmon-publisher      # copy the tk_... token
# Your phone: read-only on the topic
docker compose exec ntfy ntfy user add phone
docker compose exec ntfy ntfy access phone netmon-alerts read-only
```

Then in `.env` set `NOTIFY_NTFY_ENABLED=true` and `NOTIFY_NTFY_TOKEN=tk_...`,
run `docker compose up -d`, and send a test message:

```bash
docker compose exec web python manage.py send_test_notification
```

In the ntfy phone app, add a subscription to topic `netmon-alerts` on server
`NTFY_PUBLIC_URL` (e.g. `http://192.168.31.234:8080`) and log in as `phone`.

* **iPhone:** iOS only delivers instantly via Apple push, which ntfy routes
  through ntfy.sh. Set `NTFY_UPSTREAM_BASE_URL=https://ntfy.sh`; only a
  content-free "poll" nudge goes out, and the message itself is fetched from
  your server.
* **Away from home:** use Twingate (above) so the phone can reach ntfy.
* **Static IP:** links and the ntfy server use this PC's LAN IP. Reserve it
  in the Xiaomi router's DHCP settings so it doesn't change.

| Alert | ntfy priority |
|---|---|
| `new_device` (warning), `check_failed` (warning) | 4 (high) |
| `new_random_mac` (info) | 2 (low); disable with `NOTIFY_RANDOM_MAC=false` |
| `host_offline` (critical) | 5 (max) |

* Each alert is sent once per channel, after it is committed to the database.
  Notifications link to the device/host page (`SITE_URL`).
* **Cooldown:** a repeat alert of the same kind for the same device/host within
  `NOTIFY_COOLDOWN_MINUTES` (default 60) is marked *suppressed* and not sent.
* **Retry:** network errors, HTTP 5xx and 429 are retried up to 5 times with
  exponential backoff (30 s, 60 s, ... capped at 15 min). Auth/config errors
  (4xx) fail at once; see the alert's `notify_error` in the admin.
* Admin action **Send notification now** resends selected alerts, ignoring the cooldown.
* Email works the same way: `NOTIFY_EMAIL_ENABLED=true`, `NOTIFY_EMAIL_TO`,
  and the SMTP `EMAIL_*` settings.

## Scanner CLI

```bash
.venv/bin/python -m scanner detect                          # auto-detected subnet + interface
sudo .venv/bin/python -m scanner scan                       # one-off local scan, prints a table
sudo .venv/bin/python -m scanner scan 192.168.31.0/24 --json
.venv/bin/python -m scanner lookup b8:27:eb:12:34:56        # vendor lookup, no privileges needed
.venv/bin/python -m scanner agent --help                    # the scanner agent (see above)
```

Useful `scan` options: `-i/--iface`, `-b/--backend {auto,raw,scapy,arp-scan}`,
`-t/--timeout`, `-r/--retries`, `--no-resolve` (skip reverse DNS), `--json`.
Safety rails: only private IPv4 ranges are accepted, and anything larger than a
`/22` needs `--allow-large`. Exit code `77` means the scan lacked privileges.

## Scanner privileges

ARP discovery sends raw Ethernet frames, so it needs elevated access, and it
must run **on a machine attached to the LAN being scanned** (ARP does not cross
routers or VPNs).

| Platform | Option |
|---|---|
| macOS | `sudo scripts/run_scanner.sh`, **or** give your user read/write on `/dev/bpf*` (Wireshark's *ChmodBPF* helper, `access_bpf` group) and run it without sudo. |
| Linux, systemd | `AmbientCapabilities=CAP_NET_RAW` for an unprivileged user (the shipped unit does this). |
| Linux, Docker | `--network host --cap-drop ALL --cap-add NET_RAW`. |
| Linux, ad hoc | `sudo`, or `setcap cap_net_raw+eip` on the **real** interpreter binary (affects every script it runs, so prefer the systemd unit). |
| Docker Desktop (macOS/Windows) | Containers run inside a VM, so **host networking cannot see your LAN at layer 2**. Run the agent natively instead. |

## Tests

```bash
.venv/bin/python -m pytest          # backend + agents; in-memory SQLite by default
(cd frontend && npm test && npm run typecheck)
TEST_DATABASE_URL=postgres://netmon:PASSWORD@localhost:5432/netmon .venv/bin/python -m pytest
```
