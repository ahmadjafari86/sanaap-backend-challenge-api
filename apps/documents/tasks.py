"""Broker messages carry a document identifier, never file bytes or credentials."""

from celery import shared_task
from django.db import DatabaseError

from .background import process_staged_document


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True, max_retries=3, ignore_result=True)
def store_document(self, document_id):
    try:
        retry = process_staged_document(document_id)
    except DatabaseError:
        # Keep database/connection exception details out of Celery retry logs.
        raise self.retry(exc=RuntimeError("Document database temporarily unavailable"), countdown=2) from None
    if retry:
        raise self.retry(countdown=min(2 ** self.request.retries, 8))
