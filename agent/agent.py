#!/usr/bin/env python3
"""Network Monitor host agent: reports hostname, uptime, CPU, memory and disk.

Single file, Python 3.9+ standard library only: copy it to any Linux (Pi, NAS,
VPS) or macOS machine and run it. Needs no privileges.

    NETMON_SERVER_URL=https://monitor.example NETMON_TOKEN=... python3 agent.py
    python3 agent.py --config /etc/netmon/agent.env
    python3 agent.py --print        # show the metrics, send nothing

Settings (env vars or a KEY=VALUE file via --config, the file wins):
    NETMON_SERVER_URL   Network Monitor URL, e.g. http://192.168.31.234:8000
    NETMON_TOKEN        this host's agent token (or NETMON_TOKEN_FILE=path)
    NETMON_INTERVAL     seconds between check-ins (default 60)
    NETMON_DISK_PATH    filesystem to report (default /)
    NETMON_CA_FILE      CA bundle for HTTPS with a private CA (optional)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

VERSION = "1.0.0"
logger = logging.getLogger("netmon-agent")
MAX_BACKOFF = 300.0


# --- Metrics ---------------------------------------------------------------------------------

def parse_proc_stat(text: str) -> tuple[int, int]:
    """(idle, total) jiffies from the aggregate 'cpu' line of /proc/stat."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
            return idle, sum(values[:8])
    raise ValueError("no cpu line in /proc/stat")


def cpu_percent_between(before: tuple[int, int], after: tuple[int, int]) -> float | None:
    idle = after[0] - before[0]
    total = after[1] - before[1]
    if total <= 0:
        return None
    return round(100.0 * (1 - idle / total), 1)


def parse_meminfo(text: str) -> float | None:
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if rest:
            values[key.strip()] = int(rest.split()[0])
    total = values.get("MemTotal")
    available = values.get("MemAvailable", values.get("MemFree"))
    if not total or available is None:
        return None
    return round(100.0 * (1 - available / total), 1)


def parse_vm_stat(text: str, total_bytes: int) -> float | None:
    """macOS: used = active + wired + compressed pages."""
    page = int(m.group(1)) if (m := re.search(r"page size of (\d+) bytes", text)) else 4096
    pages = {k.strip(): int(v.strip().rstrip(".")) for k, v in re.findall(r"^(Pages [^:]+):\s+(\d+\.?)", text, re.M)}
    used = sum(pages.get(k, 0) for k in ("Pages active", "Pages wired down", "Pages occupied by compressor"))
    if not total_bytes:
        return None
    return round(100.0 * used * page / total_bytes, 1)


def parse_top_cpu(text: str) -> float | None:
    """macOS `top -l 1` line: 'CPU usage: 5.12% user, 8.33% sys, 86.54% idle'."""
    match = re.search(r"CPU usage:.*?([\d.]+)% idle", text)
    return round(100.0 - float(match.group(1)), 1) if match else None


def parse_boottime(text: str, now: float) -> int | None:
    """macOS `sysctl -n kern.boottime`: '{ sec = 1696000000, usec = 0 } ...'."""
    match = re.search(r"sec = (\d+)", text)
    return int(now - int(match.group(1))) if match else None


def _read(path: str) -> str:
    with open(path) as fh:
        return fh.read()


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True).stdout


class Collector:
    """Collects metrics; CPU on Linux is measured between consecutive calls."""

    def __init__(self, disk_path: str = "/"):
        self.disk_path = disk_path
        self.system = platform.system()
        self._last_stat: tuple[int, int] | None = None

    def collect(self) -> dict:
        metrics: dict = {"hostname": socket.gethostname(), "agent_version": VERSION}
        try:
            usage = shutil.disk_usage(self.disk_path)
            metrics["disk_percent"] = round(100.0 * usage.used / usage.total, 1)
        except OSError as exc:
            logger.debug("disk usage unavailable: %s", exc)
        try:
            if self.system == "Linux":
                metrics.update(self._linux())
            elif self.system == "Darwin":
                metrics.update(self._macos())
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            logger.warning("Some metrics unavailable: %s", exc)
        return metrics

    def _linux(self) -> dict:
        out = {"uptime_seconds": int(float(_read("/proc/uptime").split()[0]))}
        out["mem_percent"] = parse_meminfo(_read("/proc/meminfo"))
        stat = parse_proc_stat(_read("/proc/stat"))
        if self._last_stat is None:  # first run: sample over one second
            time.sleep(1)
            self._last_stat, stat = stat, parse_proc_stat(_read("/proc/stat"))
        out["cpu_percent"] = cpu_percent_between(self._last_stat, stat)
        self._last_stat = stat
        return out

    def _macos(self) -> dict:
        total = int(_run("sysctl", "-n", "hw.memsize").strip())
        return {
            "uptime_seconds": parse_boottime(_run("sysctl", "-n", "kern.boottime"), time.time()),
            "mem_percent": parse_vm_stat(_run("vm_stat"), total),
            "cpu_percent": parse_top_cpu(_run("top", "-l", "1", "-n", "0", "-s", "0")),
        }


# --- Config / transport ------------------------------------------------------------------

class ConfigError(Exception):
    pass


def read_env_file(path: str) -> dict:
    values = {}
    with open(path) as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key.strip().removeprefix("export ").strip()] = value
    return values


def load_config(path: str | None = None, env: dict | None = None) -> dict:
    values = dict(os.environ if env is None else env)
    if path:
        values.update(read_env_file(path))
    server = values.get("NETMON_SERVER_URL", "").rstrip("/")
    token = values.get("NETMON_TOKEN", "")
    if not token and values.get("NETMON_TOKEN_FILE"):
        with open(values["NETMON_TOKEN_FILE"]) as fh:
            token = fh.read().strip()
    if not server.startswith(("http://", "https://")):
        raise ConfigError("NETMON_SERVER_URL must be set to http(s)://host:port")
    if not token:
        raise ConfigError("NETMON_TOKEN (or NETMON_TOKEN_FILE) must be set")
    try:
        interval = max(10, int(values.get("NETMON_INTERVAL") or 60))
    except ValueError as exc:
        raise ConfigError("NETMON_INTERVAL must be a number of seconds") from exc
    return {
        "server": server,
        "token": token,
        "interval": interval,
        "disk_path": values.get("NETMON_DISK_PATH") or "/",
        "ca_file": values.get("NETMON_CA_FILE") or None,
    }


class CheckinError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


def send_checkin(cfg: dict, metrics: dict, timeout: float = 15.0) -> dict:
    request = urllib.request.Request(
        cfg["server"] + "/api/agent/checkin/",
        data=json.dumps(metrics).encode(),
        headers={
            "Authorization": f"Bearer {cfg['token']}",
            "Content-Type": "application/json",
            "User-Agent": f"netmon-agent/{VERSION}",
        },
        method="POST",
    )
    context = ssl.create_default_context(cafile=cfg["ca_file"]) if cfg.get("ca_file") else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", "")
        except (ValueError, AttributeError):
            detail = ""
        raise CheckinError(exc.code, detail or str(exc.reason)) from exc


def run_forever(cfg: dict, stop: threading.Event, collector: Collector | None = None, send=send_checkin) -> None:
    collector = collector or Collector(cfg["disk_path"])
    backoff = 5.0
    logger.info("netmon agent %s reporting to %s every %ss", VERSION, cfg["server"], cfg["interval"])
    while not stop.is_set():
        try:
            send(cfg, collector.collect())
            wait, backoff = cfg["interval"], 5.0
        except CheckinError as exc:
            if exc.status in (401, 403):
                logger.error("Server rejected this agent (%s): check the token.", exc)
                wait = max(cfg["interval"], 60)
            else:
                logger.warning("Check-in failed: %s", exc)
                wait, backoff = min(backoff, cfg["interval"]), min(backoff * 2, MAX_BACKOFF)
        except (OSError, ValueError) as exc:
            logger.warning("Cannot reach %s: %s", cfg["server"], exc)
            wait, backoff = min(backoff, cfg["interval"]), min(backoff * 2, MAX_BACKOFF)
        stop.wait(wait)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Network Monitor host agent")
    parser.add_argument("-c", "--config", help="KEY=VALUE file with NETMON_* settings")
    parser.add_argument("--once", action="store_true", help="check in once and exit")
    parser.add_argument("--print", action="store_true", help="print metrics and exit (sends nothing)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.print:
        print(json.dumps(Collector(os.environ.get("NETMON_DISK_PATH", "/")).collect(), indent=2))
        return 0
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.once:
        print(json.dumps(send_checkin(cfg, Collector(cfg["disk_path"]).collect())))
        return 0
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run_forever(cfg, stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
