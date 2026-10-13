"""Command-line interface: ``python -m scanner <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from scanner.arp_scan import BACKENDS, ScanError, PermissionDeniedError, detect_local_subnet, scan
from scanner.oui import DEFAULT_DB_PATH, OuiDatabase, update_database


def _format_table(hosts) -> str:
    headers = ("IP", "MAC", "VENDOR", "HOSTNAME")
    rows = [
        (
            h.ip,
            h.mac,
            h.vendor or ("(private/random MAC)" if h.locally_administered else "-"),
            h.hostname or "-",
        )
        for h in hosts
    ]
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    lines = ["  ".join(str(c).ljust(w) for c, w in zip(row, widths)).rstrip() for row in [headers, *rows]]
    return "\n".join(lines)


def cmd_scan(args) -> int:
    subnet, iface = args.subnet, args.iface
    if subnet is None:
        subnet, detected_iface = detect_local_subnet()
        iface = iface or detected_iface
        print(f"Auto-detected subnet {subnet} on {iface}", file=sys.stderr)

    hosts = scan(
        subnet,
        backend=args.backend,
        iface=iface,
        timeout=args.timeout,
        retries=args.retries,
        resolve=not args.no_resolve,
        vendor_db=OuiDatabase.load(args.oui_db),
        allow_large=args.allow_large,
    )
    if args.json:
        print(json.dumps([h.to_dict() for h in hosts], indent=2))
    else:
        print(_format_table(hosts))
        print(f"\n{len(hosts)} device(s) found on {subnet}", file=sys.stderr)
    return 0


def cmd_detect(args) -> int:
    subnet, iface = detect_local_subnet()
    print(f"{subnet} {iface}")
    return 0


def cmd_update_oui(args) -> int:
    count = update_database(args.oui_db)
    print(f"Wrote {count} OUI entries to {args.oui_db}")
    return 0


def cmd_lookup(args) -> int:
    db = OuiDatabase.load(args.oui_db)
    for mac in args.macs:
        print(f"{mac}  {db.lookup(mac) or '-'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scanner", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "--oui-db", type=Path, default=DEFAULT_DB_PATH, help="path to the OUI CSV (default: %(default)s)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="ARP-scan a subnet (needs root / raw socket access)")
    p.add_argument("subnet", nargs="?", help="CIDR to scan, e.g. 192.168.1.0/24 (default: auto-detect)")
    p.add_argument("-i", "--iface", help="network interface to use")
    p.add_argument("-b", "--backend", choices=BACKENDS, default="auto")
    p.add_argument("-t", "--timeout", type=float, default=2.0, help="seconds to wait for replies")
    p.add_argument("-r", "--retries", type=int, default=1, help="extra ARP rounds for missed hosts")
    p.add_argument("--no-resolve", action="store_true", help="skip reverse-DNS hostname lookup")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("--allow-large", action="store_true", help="allow subnets larger than /22")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("detect", help="print the auto-detected subnet and interface")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("update-oui", help="download the IEEE OUI vendor database")
    p.set_defaults(func=cmd_update_oui)

    p = sub.add_parser("lookup", help="look up the vendor for one or more MACs")
    p.add_argument("macs", nargs="+")
    p.set_defaults(func=cmd_lookup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except PermissionDeniedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 77  # EX_NOPERM
    except ScanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
