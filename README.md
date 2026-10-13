# Network Device Monitor

Self-hosted monitor for **your own** network: discovers LAN devices via ARP and
alerts on unknown ones, and tracks remote hosts through lightweight agents.

> Status: **Phase 2** — scanner CLI, Django models/admin, Docker Compose.

## Architecture

```
 Docker Compose (this Mac)                         Native on the Mac (on the LAN)
 ┌──────────────────────────────────────────┐      ┌───────────────────────────────┐
 │ web (Django)   worker (queue: default)   │      │ scanner worker (queue: scanner)│
 │ beat (schedules)                         │◄────►│ needs raw-socket privileges    │
 │ db (Postgres :5432)  redis (:6379)       │      │ ARP-scans 192.168.31.0/24      │
 └──────────────────────────────────────────┘      └───────────────────────────────┘
        db/redis/web are published on 127.0.0.1 only
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
.venv/bin/python -m pytest          # in-memory SQLite by default
TEST_DATABASE_URL=postgres://netmon:PASSWORD@localhost:5432/netmon .venv/bin/python -m pytest
```
