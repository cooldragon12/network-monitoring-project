#!/bin/sh
# Run the LAN scanner Celery worker natively on a machine attached to the LAN.
# Needs raw-socket access: run with sudo, or grant your user /dev/bpf* access
# on macOS (see README "Scanner privileges").
set -eu
cd "$(dirname "$0")/.."
# Avoid root-owned __pycache__ files when run with sudo.
export PYTHONDONTWRITEBYTECODE=1
exec .venv/bin/celery -A config worker -Q scanner --concurrency 1 -n "scanner@%h" -l info "$@"
