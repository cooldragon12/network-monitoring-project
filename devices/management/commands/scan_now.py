import json

from django.core.management.base import BaseCommand

from devices.tasks import perform_lan_scan


class Command(BaseCommand):
    help = "Run one LAN scan synchronously (needs raw-socket privileges, e.g. sudo)."

    def handle(self, *args, **options):
        result = perform_lan_scan()
        self.stdout.write(json.dumps(result, indent=2))
