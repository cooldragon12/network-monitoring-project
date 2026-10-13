from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from monitoring.notifications import CHANNELS, Message, NotificationError, channel_config, enabled_channels


class Command(BaseCommand):
    help = "Send a test message on every enabled notification channel."

    def handle(self, *args, **options):
        channels = enabled_channels()
        if not channels:
            raise CommandError("No notification channels enabled (see NOTIFY_* in .env).")
        message = Message(
            title="Network Monitor test",
            body="If you can read this, notifications work.",
            priority=3,
            tags=("white_check_mark",),
            url=settings.SITE_URL,
        )
        failed = False
        for channel in channels:
            try:
                CHANNELS[channel](message, channel_config(channel))
                self.stdout.write(self.style.SUCCESS(f"{channel}: sent"))
            except NotificationError as exc:
                failed = True
                self.stderr.write(self.style.ERROR(f"{channel}: {exc}"))
        if failed:
            raise CommandError("One or more channels failed.")
