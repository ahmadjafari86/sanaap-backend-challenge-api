"""Broker messages carry a document identifier, never file bytes or credentials."""

from celery import shared_task
from django.db import DatabaseError

from .outbox.uploads import process_outbox_upload, resolve_upload_message


@shared_task(acks_late=True, reject_on_worker_lost=True, ignore_result=True)
def store_document(document_id, event_id=None):
    try:
        message = resolve_upload_message(document_id=document_id, event_id=event_id)
        if message is not None:
            process_outbox_upload(document_id=document_id, event_id=message.pk)
    except DatabaseError:
        # Recovery re-arms the unreceipted event; no unrecorded retry publication.
        raise RuntimeError("Document database temporarily unavailable") from None
