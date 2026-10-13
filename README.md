# Network Device Monitor

Self-hosted monitor for **your own** network: discovers LAN devices via ARP and
alerts on unknown ones, and tracks remote hosts through lightweight agents.

> Status: **Phase 5** — React dashboard and REST API.

## Architecture

```
 Docker Compose (this Mac)                         Native on the Mac (on the LAN)
 ┌──────────────────────────────────────────┐      ┌───────────────────────────────┐
 │ web (Django)   worker (queue: default)   │      │ scanner worker (queue: scanner)│
 │ beat (schedules)  ntfy (:8080)           │◄────►│ needs raw-socket privileges    │
 │ db (Postgres :5432)  redis (:6379)       │      │ ARP-scans 192.168.31.0/24      │
 └──────────────────────────────────────────┘      └───────────────────────────────┘
   db/redis on 127.0.0.1 only; web/ntfy on WEB_BIND/NTFY_BIND (default 127.0.0.1)
```

Scans run only in the `scanner` Celery queue, never inside a web request.
Docker Desktop on macOS cannot see the LAN at layer 2, so the scanner worker
runs natively. On a Linux host, `docker compose --profile linux-scanner up`
runs it in a container instead (`network_mode: host` + `NET_RAW`/`NET_ADMIN`).

**Double NAT note:** this network sits behind a Xiaomi router (192.168.31.1),
which itself sits behind the main router (192.168.1.1). ARP does not cross
routers, so devices on 192.168.1.0/24 are invisible from here. To monitor that
segment, run a second scanner on a machine connected to it.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m scanner update-oui   # downloads IEEE vendor DB (a few MB) to scanner/data/oui.csv

cp .env.example .env                     # then fill in DJANGO_SECRET_KEY and POSTGRES_PASSWORD
                                         # (and the same password inside DATABASE_URL)
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Admin: <http://127.0.0.1:8000/admin/>. For plain-HTTP local development set
`DJANGO_DEBUG=true` and `DJANGO_SECURE_COOKIES=false` in `.env`; production
defaults are DEBUG off and secure cookies on.

All configuration is via environment variables; see `.env.example`. Native
processes (`manage.py`, the scanner worker) read `.env` automatically; real
environment variables take precedence.

## Running the LAN scanner

With the Docker stack up, start the scanner worker natively on the Mac:

```bash
sudo scripts/run_scanner_worker.sh
```

`beat` (in Docker) enqueues `run_lan_scan` every `SCAN_INTERVAL_MINUTES`
(default 2) onto the `scanner` queue; this worker consumes it via
`localhost:6379`. Scans that were not picked up before the next one is due are
dropped instead of piling up. Celery prints a warning when run as root; to avoid
sudo, grant your user `/dev/bpf*` access (see below) and run the script directly.

One-off scan without Celery (useful for checking privileges/config):

```bash
sudo PYTHONDONTWRITEBYTECODE=1 .venv/bin/python manage.py scan_now
```

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
  mark your whole network offline.

In the admin, select devices and use **Mark selected as known**; this also
acknowledges their open new-device alerts.

The scanning machine itself never appears in results (it does not ARP-reply to
itself).

## Dashboard

A React single-page app (TypeScript, Vite, Tailwind CSS v4, Motion) in
`frontend/`, built to static files that Django serves at `/` (same origin, so
the session cookie and CSRF protection just work; no tokens in the browser).

* Device table with Online/Offline/Unknown filters and search; unknown devices
  are highlighted and can be bulk-approved ("Mark as known").
* Device page: rename, set type and notes, mark known, forget, alerts, and the
  event history timeline.
* Alerts page: open/all, acknowledge one or all.
* API tokens page.
* Follows the system light/dark theme and `prefers-reduced-motion`; works on phones.
* Data refreshes every 15 s (live WebSocket updates are Phase 7).

Every page and API call requires login. Create your account with
`docker compose exec web python manage.py createsuperuser`.

The Docker image builds the frontend (multi-stage). For frontend development
with hot reload, run Django (Docker or `manage.py runserver`) and:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api and /admin to Django on :8000
```

To serve a local build from Django without Docker: `npm run build` (outputs `frontend/dist`).

## REST API

All endpoints live under `/api/` and require authentication: a browser
session (with `X-CSRFToken` on unsafe methods), or a personal API token:

```bash
curl -H "Authorization: Bearer $TOKEN" http://192.168.31.234:8000/api/devices/?known=false
```

Create tokens on the dashboard's **API tokens** page. They are shown once,
stored only as a SHA-256 hash, and can be revoked; tokens cannot create tokens.

| Endpoint | Methods | Notes |
|---|---|---|
| `devices/` | GET | `status=online\|offline`, `known=`, `random=`, `search=`, `ordering=` |
| `devices/{id}/` | GET, PATCH, DELETE | PATCH `label`, `device_type`, `notes`, `is_known` (true also acks its new-device alerts) |
| `devices/{id}/events/` | GET | event history |
| `devices/mark-known/` | POST | `{"ids": [1, 2]}` |
| `alerts/` | GET | `acknowledged=`, `kind=`, `severity=`, `device=` |
| `alerts/{id}/acknowledge/`, `alerts/acknowledge-all/` | POST | |
| `events/` | GET | `device=`, `event_type=` |
| `scans/` | GET | scan runs |
| `stats/`, `meta/` | GET | dashboard summary, choice lists |
| `auth/session/`, `auth/login/`, `auth/logout/` | GET/POST | login is rate-limited (`LOGIN_THROTTLE_RATE`, default 10/min) |
| `tokens/` | GET, POST, DELETE | browser session only |

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
* **Away from home:** ntfy and the dashboard are LAN-only, so your phone gets
  alerts only on home Wi-Fi unless you add Tailscale/WireGuard (Phase 6 notes).
* **Static IP:** links and the ntfy server use this Mac's LAN IP. Reserve it
  in the Xiaomi router's DHCP settings so it doesn't change.

Behaviour:

| Alert | ntfy priority |
|---|---|
| `new_device` (warning) | 4 (high) |
| `new_random_mac` (info) | 2 (low); disable with `NOTIFY_RANDOM_MAC=false` |
| `host_offline` (critical, Phase 6) | 5 (max) |

* Each alert is sent once per channel, after it is committed to the database.
  Notifications link to the device page (`SITE_URL`).
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
.venv/bin/python -m scanner detect                          # show auto-detected subnet + interface
sudo .venv/bin/python -m scanner scan                       # scan the auto-detected subnet
sudo .venv/bin/python -m scanner scan 192.168.31.0/24 --json
.venv/bin/python -m scanner lookup b8:27:eb:12:34:56        # vendor lookup, no privileges needed
```

Useful `scan` options: `-i/--iface`, `-b/--backend {auto,scapy,arp-scan}`,
`-t/--timeout`, `-r/--retries`, `--no-resolve` (skip reverse DNS), `--json`.

Safety rails: only private IPv4 ranges are accepted, and anything larger than a
`/22` needs `--allow-large`. Exit code `77` means the scan lacked privileges.

Output fields: `mac`, `ip`, `hostname` (reverse DNS, often blank on home LANs),
`vendor` (offline OUI lookup), and `locally_administered`: true for
randomized/private MACs (iOS/Android/Windows Wi-Fi privacy), which have no
vendor and can change per network or over time.

## Scanner privileges

ARP discovery sends raw Ethernet frames, so it needs elevated access. The scan
must also run **on a machine attached to the LAN being scanned**: ARP does not
cross routers or VPNs.

| Platform | Option |
|---|---|
| macOS | `sudo .venv/bin/python -m scanner scan`, **or** give your user read/write on `/dev/bpf*` (Wireshark's *ChmodBPF* helper does this via the `access_bpf` group), then no sudo needed. |
| Linux (sudo) | `sudo .venv/bin/python -m scanner scan` |
| Linux (capabilities) | `sudo setcap cap_net_raw,cap_net_admin+eip "$(readlink -f .venv/bin/python)"`. Note this applies to the **real interpreter binary** (the venv's `python` is a symlink), so every script run by that interpreter gets the capability. Prefer a dedicated interpreter or a dedicated service user. |
| Docker (Linux host) | The scanner container needs `network_mode: host` and `cap_add: [NET_RAW, NET_ADMIN]`. |
| Docker Desktop (macOS/Windows) | Containers run inside a VM, so **host networking cannot see your LAN at Layer 2**. Run the scanner natively on the Mac, or on a Linux box (e.g. a Pi) on the LAN. |

The optional `arp-scan` backend (`brew install arp-scan` / `apt install arp-scan`)
needs the same privileges.

## Tests

```bash
.venv/bin/python -m pytest          # backend; in-memory SQLite by default
(cd frontend && npm test && npm run typecheck)
TEST_DATABASE_URL=postgres://netmon:PASSWORD@localhost:5432/netmon .venv/bin/python -m pytest
```
