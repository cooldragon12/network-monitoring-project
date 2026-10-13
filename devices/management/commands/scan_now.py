import json

from django.core.management.base import BaseCommand

from devices.scanning import scan_locally


class Command(BaseCommand):
    help = "Run one LAN scan in this process (debugging; needs root/CAP_NET_RAW). Normally the scanner agent scans."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(scan_locally(), indent=2))
