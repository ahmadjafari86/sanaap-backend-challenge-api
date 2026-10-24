from unittest.mock import patch


def deliver_outbox_once():
    """Use real claims/cleanup while isolating the external Celery transport."""
    from ..outbox_delivery import relay_once
    with patch("apps.documents.tasks.store_document.apply_async"):
        relay_once()
