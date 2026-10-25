"""Strict document delivery adapters; failures propagate to the relay."""

from ..models import Document


def publish_upload(claim):
    from ..tasks import store_document

    store_document.apply_async(
        kwargs={"document_id": claim.document_id, "event_id": str(claim.event_id)},
        task_id=str(claim.event_id),
    )


def delete_object(claim):
    from ..services import default_storage

    # Stable UUID keys are never reused. A delayed compensation must not remove
    # an object owned by a later committed upload attempt.
    key = claim.payload["object_key"]
    if Document.objects.filter(object_key=key, status__in=["PENDING", "READY"]).exists():
        return
    default_storage.delete(key)


def delete_staging(claim):
    from ..background import staging_storage

    key = claim.payload["staging_key"]
    if Document.objects.filter(staging_key=key, status="PENDING").exists():
        return
    staging_storage().delete(key)
