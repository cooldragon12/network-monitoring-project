#!/bin/sh
# Run the LAN scanner agent natively (this PC / a Linux box on the LAN).
# Raw sockets need root here: sudo scripts/run_scanner.sh
# Config: scanner.env (copy from scanner.env.example).
set -eu
cd "$(dirname "$0")/.."
# Avoid root-owned __pycache__ files when run with sudo.
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" -m scanner agent --config scanner.env "$@"
