# Lightweight LAN scanner agent for a Raspberry Pi / NAS (Linux).
# Stdlib only: the raw AF_PACKET backend needs no scapy, so the image is just
# python:alpine plus ~50 KB of code. Multi-arch via the base image (arm64/armv7/amd64).
#
#   docker build -f scanner/Dockerfile -t netmon-scanner .
#   docker run -d --name netmon-scanner --network host --cap-add NET_RAW \
#     -e NETMON_SERVER_URL=http://192.168.31.234:8000 -e NETMON_TOKEN=... netmon-scanner
FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY scanner/__init__.py scanner/__main__.py scanner/agent.py scanner/arp_scan.py \
     scanner/cli.py scanner/linux_arp.py scanner/oui.py /app/scanner/

# Raw sockets need CAP_NET_RAW; grant it with --cap-add NET_RAW (runs as root
# inside the container, but with Docker's default reduced capability set).
ENTRYPOINT ["python", "-m", "scanner", "agent"]
