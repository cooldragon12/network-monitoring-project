"""Offline MAC vendor lookup backed by the IEEE registration authority CSVs.

The database is a single CSV (``scanner/data/oui.csv``) built by merging the
IEEE MA-L (24-bit), MA-M (28-bit) and MA-S (36-bit) registries. Lookups try
the longest prefix first, so sub-allocated blocks resolve to the right owner.

Refresh it with ``python -m scanner update-oui``.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import tempfile
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "oui.csv"

IEEE_SOURCES = (
    "https://standards-oui.ieee.org/oui/oui.csv",  # MA-L, 24-bit prefixes
    "https://standards-oui.ieee.org/oui28/mam.csv",  # MA-M, 28-bit prefixes
    "https://standards-oui.ieee.org/oui36/oui36.csv",  # MA-S, 36-bit prefixes
)

# Prefix lengths in hex digits, longest first.
_PREFIX_HEX_LENGTHS = (9, 7, 6)
_HEX_ONLY = re.compile(r"[^0-9A-Fa-f]")
_CACHE: dict[str, tuple[tuple[str, float | None], "OuiDatabase"]] = {}


def mac_hex(mac: str) -> str:
    """Return the 12 upper-case hex digits of a MAC, or raise ValueError."""
    digits = _HEX_ONLY.sub("", mac).upper()
    if len(digits) != 12:
        raise ValueError(f"invalid MAC address: {mac!r}")
    return digits


def is_locally_administered(mac: str) -> bool:
    """True for randomized/private MACs (e.g. phone Wi-Fi privacy addresses).

    These have no registered vendor and may change over time for the same device.
    """
    return bool(int(mac_hex(mac)[:2], 16) & 0x02)


class OuiDatabase:
    def __init__(self, entries: dict[str, str] | None = None):
        # Keys are upper-case hex prefixes of 6, 7 or 9 digits.
        self._entries = entries or {}

    def __len__(self) -> int:
        return len(self._entries)

    @classmethod
    def load(cls, path: str | os.PathLike = DEFAULT_DB_PATH) -> "OuiDatabase":
        """Load an IEEE-format CSV. A missing file yields an empty database."""
        path = Path(path)
        if not path.exists():
            logger.warning(
                "OUI database not found at %s; vendors will be blank. "
                "Run `python -m scanner update-oui` to download it.",
                path,
            )
            return cls()
        entries: dict[str, str] = {}
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                prefix = (row.get("Assignment") or "").strip().upper()
                name = (row.get("Organization Name") or "").strip()
                if len(prefix) in _PREFIX_HEX_LENGTHS and name:
                    entries[prefix] = name
        return cls(entries)

    @classmethod
    def load_cached(cls, path: str | os.PathLike = DEFAULT_DB_PATH) -> "OuiDatabase":
        """Like load(), but reuse the parsed database until the file changes."""
        path = Path(path)
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        key = (str(path), mtime)
        cached = _CACHE.get(str(path))
        if cached is None or cached[0] != key:
            cached = (key, cls.load(path))
            _CACHE[str(path)] = cached
        return cached[1]

    def lookup(self, mac: str) -> str | None:
        try:
            digits = mac_hex(mac)
        except ValueError:
            return None
        for length in _PREFIX_HEX_LENGTHS:
            vendor = self._entries.get(digits[:length])
            if vendor:
                return vendor
        return None


def update_database(
    dest: str | os.PathLike = DEFAULT_DB_PATH,
    sources: tuple[str, ...] = IEEE_SOURCES,
    timeout: float = 60.0,
) -> int:
    """Download the IEEE registries and atomically replace ``dest``.

    Returns the number of entries written.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["Registry", "Assignment", "Organization Name", "Organization Address"]
    rows: list[dict[str, str]] = []
    for url in sources:
        logger.info("Downloading %s", url)
        # IEEE rejects the default urllib User-Agent.
        req = urllib.request.Request(url, headers={"User-Agent": "network-monitor-oui-updater/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        rows.extend(
            {k: row.get(k, "") for k in fieldnames}
            for row in csv.DictReader(text.splitlines())
        )
    if not rows:
        raise RuntimeError("no OUI entries downloaded")

    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, dest)
    except BaseException:
        os.unlink(tmp)
        raise
    return len(rows)
