# Network Device Monitor

Self-hosted monitor for **your own** network: discovers LAN devices via ARP and
alerts on unknown ones, and tracks remote hosts through lightweight agents.

> Status: **Phase 1** — standalone scanner (no Django yet).

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m scanner update-oui   # downloads IEEE vendor DB (a few MB) to scanner/data/oui.csv
```

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
.venv/bin/python -m pytest
```
