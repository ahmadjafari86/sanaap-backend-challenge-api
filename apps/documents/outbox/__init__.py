"""Wire document obligations into the outbox without starting external work."""


def register_adapters():
    from apps.outbox.registry import registry

    from .events import EventKind, VALIDATORS
    from .handlers import delete_object, delete_staging, publish_upload
    from .operations import get_upload_status
    from .recovery import recover_uploads

    handlers = {
        EventKind.UPLOAD_REQUESTED: publish_upload,
        EventKind.OBJECT_DELETE: delete_object,
        EventKind.STAGING_DELETE: delete_staging,
    }
    for kind, validator in VALIDATORS.items():
        registry.register_event(kind, validator=validator, handler=handlers.get(kind))
    registry.register_adapter("documents", recover=recover_uploads, status=get_upload_status)
