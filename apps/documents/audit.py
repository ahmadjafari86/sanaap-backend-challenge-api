import logging

from django.db import transaction

from .models import DocumentAuditEvent

logger = logging.getLogger(__name__)


def record_document_event(*, actor_id, action, document_id, outcome, status_code):
    with transaction.atomic():
        return DocumentAuditEvent.objects.create(
            actor_id=actor_id,
            action=action,
            document_id=document_id,
            outcome=outcome,
            status_code=status_code,
        )


class DocumentAuditMixin:
    audit_actions = {}

    def handle_exception(self, exc):
        try:
            response = super().handle_exception(exc)
        except Exception:
            self._record_failure(500)
            raise
        self._record_failure(response.status_code)
        return response

    def _record_failure(self, status_code):
        action = self.audit_actions.get(self.request.method)
        if action is None:
            return

        # Do not restart authentication when handling an authentication error.
        user = getattr(self.request, "_user", None)
        actor_id = user.pk if user is not None and user.is_authenticated else None
        document_id = self.kwargs.get("pk")
        if not isinstance(document_id, int) or not 0 <= document_id <= 2**63 - 1:
            document_id = None

        try:
            record_document_event(
                actor_id=actor_id,
                action=action,
                document_id=document_id,
                outcome=DocumentAuditEvent.Outcome.FAILURE,
                status_code=status_code,
            )
        except Exception as error:
            logger.error("Document failure audit could not be recorded (%s).", type(error).__name__)
