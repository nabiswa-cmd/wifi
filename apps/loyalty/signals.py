import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.billing.models import Payment

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Payment)
def reverse_points_on_refund(sender, instance, **kwargs):
    """If staff mark a payment REFUNDED, take back the points it earned (idempotent)."""
    if instance.status != Payment.Status.REFUNDED:
        return
    try:
        from .services import reverse_for_payment
        reverse_for_payment(instance)
    except Exception:  # noqa: BLE001 - never let loyalty block a payment status change
        logger.exception('Could not reverse loyalty points for payment %s', instance.pk)
