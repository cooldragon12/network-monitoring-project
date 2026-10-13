from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from monitoring.models import Alert


@receiver(post_save, sender=Alert, dispatch_uid="alert_created_notify")
def notify_on_alert_created(sender, instance, created, **kwargs):
    if not created:
        return
    from monitoring.tasks import dispatch_alert_notifications

    # Enqueue only once the alert is committed. robust=True: a broker outage is
    # logged instead of breaking the scan that created the alert.
    transaction.on_commit(lambda: dispatch_alert_notifications.delay(instance.pk), robust=True)
