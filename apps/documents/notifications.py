"""Best-effort hints; database metadata remains the source of truth."""

import asyncio
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)
DOCUMENT_GROUP = "documents.notifications"
PUBLICATION_TIMEOUT = 2


async def _send(document_id, event):
    await asyncio.wait_for(get_channel_layer().group_send(DOCUMENT_GROUP, {
        "type": "document.event", "event": event, "document_id": document_id,
    }), timeout=PUBLICATION_TIMEOUT)


def notify_document(document_id, event):
    try:
        async_to_sync(_send)(document_id, event)
    except Exception as error:
        # Never turn a committed mutation into an HTTP/worker failure or log JWTs.
        logger.warning("Document notification publication failed (%s).", type(error).__name__)
