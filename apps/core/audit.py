"""
Tiny helper so every app writes AuditLog entries the same way (Section 25),
without importing Django's request/response machinery into model code.
"""
from .models import AuditLog


def log_action(actor, action, obj=None, previous_value=None, new_value=None, ip_address=None):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        object_type=obj.__class__.__name__ if obj is not None else '',
        object_id=str(getattr(obj, 'pk', '')) if obj is not None else '',
        previous_value=previous_value,
        new_value=new_value,
        ip_address=ip_address,
    )
