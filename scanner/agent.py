"""LAN scanner agent: ``python -m scanner agent``.

A small standalone process for any machine on the LAN (this PC, a Raspberry
Pi, a NAS). It needs only the Python standard library on Linux (raw AF_PACKET
backend) and never talks to the database or Redis: it heartbeats the Network
Monitor server over HTTP(S), scans when the server says a scan is due (on
schedule or "Scan now"), and posts the results back.

Configuration (KEY=VALUE file via --config, or environment variables):
  NETMON_SERVER_URL   e.g. http://192.168.31.234:8000
  NETMON_TOKEN        the host's agent token (or NETMON_TOKEN_FILE=path)
  NETMON_INTERFACE    optional, network interface to scan on
  NETMON_BACKEND      optional: auto | raw | scapy | arp-scan
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scanner.arp_scan import BACKENDS, ScanError, detect_local_subnet, scan
from scanner.oui import OuiDatabase

VERSION = "1.0.0"
logger = logging.getLogger("scanner.agent")

MAX_BACKOFF = 60.0
AUTH_FAILURE_WAIT = 60.0


class ConfigError(Exception):
    pass


class AgentHTTPError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


@dataclass
class AgentConfig:
    server_url: str
    token: str
    interface: str | None = None
    backend: str = "auto"
    ca_file: str | None = None


def read_env_file(path: str | os.PathLike) -> dict[str, str]:
    values = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.removeprefix("export ").strip()] = value
    return values


def load_config(path: str | None = None, env: dict[str, str] | None = None, **overrides) -> AgentConfig:
    """Config file values win over environment variables; CLI overrides win over both."""
    values = dict(os.environ if env is None else env)
    if path:
        values.update(read_env_file(path))
    values.update({k: v for k, v in overrides.items() if v})

    server = values.get("NETMON_SERVER_URL", "").rstrip("/")
    token = values.get("NETMON_TOKEN", "")
    if not token and values.get("NETMON_TOKEN_FILE"):
        token = Path(values["NETMON_TOKEN_FILE"]).read_text().strip()
    if not server.startswith(("http://", "https://")):
        raise ConfigError("NETMON_SERVER_URL must be set to http(s)://host:port")
    if not token:
        raise ConfigError("NETMON_TOKEN (or NETMON_TOKEN_FILE) must be set")
    backend = values.get("NETMON_BACKEND", "auto") or "auto"
    if backend not in BACKENDS:
        raise ConfigError(f"NETMON_BACKEND must be one of {BACKENDS}")
    return AgentConfig(
        server_url=server,
        token=token,
        interface=values.get("NETMON_INTERFACE") or None,
        backend=backend,
        ca_file=values.get("NETMON_CA_FILE") or None,
    )


class ApiClient:
    def __init__(self, cfg: AgentConfig, timeout: float = 15.0):
        self.base = cfg.server_url + "/api/"
        self.token = cfg.token
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=cfg.ca_file) if cfg.ca_file else None

    def post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"netmon-scanner/{VERSION}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail", "")
            except (ValueError, AttributeError):
                detail = ""
            raise AgentHTTPError(exc.code, detail or exc.reason) from exc


class ScannerAgent:
    def __init__(
        self,
        cfg: AgentConfig,
        client: ApiClient,
        *,
        scan_fn: Callable = scan,
        detect_fn: Callable = detect_local_subnet,
    ):
        self.cfg = cfg
        self.client = client
        self.scan_fn = scan_fn
        self.detect_fn = detect_fn
        self.hostname = socket.gethostname()

    def run_once(self) -> float:
        """One heartbeat (and a scan if due). Returns seconds until the next one."""
        beat = self.client.post(
            "agent/scanner/heartbeat/", {"hostname": self.hostname, "agent_version": VERSION}
        )
        if beat.get("scan_now"):
            self.scan(beat)
        return float(beat.get("heartbeat_seconds") or 5)

    def scan(self, beat: dict) -> dict | None:
        subnet, iface = beat.get("subnet"), self.cfg.interface
        if not subnet:
            try:
                subnet, detected = self.detect_fn()
            except ScanError as exc:
                logger.error("Cannot auto-detect the subnet (set it on the server): %s", exc)
                return None
            iface = iface or detected
        run = self.client.post("agent/scanner/scans/", {"subnet": subnet})
        started = time.monotonic()
        logger.info("Scan %s of %s started", run["id"], run["subnet"])
        try:
            hosts = self.scan_fn(
                run["subnet"],
                backend=self.cfg.backend,
                iface=iface,
                timeout=float(beat.get("timeout_seconds") or 2),
                vendor_db=OuiDatabase(),  # vendors are resolved server-side
            )
            payload = {
                "status": "success",
                "hosts": [{"mac": h.mac, "ip": h.ip, "hostname": h.hostname} for h in hosts],
            }
        except ScanError as exc:
            logger.error("Scan failed: %s", exc)
            payload = {"status": "failed", "error": str(exc)}
        except Exception as exc:  # never let one bad scan kill the agent
            logger.exception("Unexpected scan error")
            payload = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        payload["duration_seconds"] = round(time.monotonic() - started, 3)
        result = self.client.post(f"agent/scanner/scans/{run['id']}/", payload)
        logger.info(
            "Scan %s %s: %s device(s)%s",
            result.get("id"),
            result.get("status"),
            result.get("devices_found"),
            f", {result['new_devices']} new" if result.get("new_devices") else "",
        )
        return result

    def run_forever(self, stop: threading.Event) -> None:
        backoff = 1.0
        logger.info("Scanner agent %s reporting to %s", VERSION, self.cfg.server_url)
        while not stop.is_set():
            try:
                wait = self.run_once()
                backoff = 1.0
            except AgentHTTPError as exc:
                if exc.status in (401, 403):
                    logger.error("Server rejected this agent (%s). Check the token and that the host is a scanner.", exc)
                    wait = AUTH_FAILURE_WAIT
                else:
                    logger.warning("Server error: %s", exc)
                    wait, backoff = backoff, min(backoff * 2, MAX_BACKOFF)
            except (OSError, ValueError) as exc:
                logger.warning("Cannot reach %s: %s", self.cfg.server_url, exc)
                wait, backoff = backoff, min(backoff * 2, MAX_BACKOFF)
            stop.wait(wait)
        logger.info("Scanner agent stopped")


def run(cfg: AgentConfig, once: bool = False) -> int:
    agent = ScannerAgent(cfg, ApiClient(cfg))
    if once:
        agent.run_once()
        return 0
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    agent.run_forever(stop)
    return 0
