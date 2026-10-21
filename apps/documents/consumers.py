"""Access-token-authenticated, read-only hints for shared document metadata."""

import asyncio
import json
import logging
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user_model
from redis.exceptions import RedisError
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.utils import aware_utcnow

from .notifications import DOCUMENT_GROUP

logger = logging.getLogger(__name__)


@database_sync_to_async
def validate_identity(raw=None, token=None):
    authentication = JWTAuthentication()
    if token is None:
        token = authentication.get_validated_token(raw)
    token.check_exp(current_time=aware_utcnow())
    user = authentication.get_user(token)
    if user.role not in get_user_model().Role.values:
        raise PermissionDenied()
    return token


class DocumentNotificationsConsumer(AsyncWebsocketConsumer):
    async def __call__(self, scope, receive, send):
        self.closed = False
        self.authenticated = False
        self.token = None
        self.auth_timer = self.expiry_timer = None
        self.cleaned = False
        try:
            await super().__call__(scope, receive, send)
        except (RedisError, OSError, TimeoutError) as error:
            logger.warning("WebSocket channel receive failed (%s).", type(error).__name__)
            if not self.closed:
                await self.terminate(1013)
        finally:
            await self.disconnect(1000)

    async def connect(self):
        if self.scope.get("query_string"):
            await self.terminate(4400)
            return
        await self.accept()
        self.auth_timer = asyncio.create_task(self.expire_after(settings.WEBSOCKET_AUTH_TIMEOUT))

    async def expire_after(self, seconds):
        await asyncio.sleep(max(0, seconds))
        await self.terminate(4401)

    async def terminate(self, code):
        if self.closed:
            return
        self.closed = True
        self.authenticated = False
        for timer in (self.auth_timer, self.expiry_timer):
            if timer is not None and timer is not asyncio.current_task():
                timer.cancel()
        await self.close(code=code)

    async def authorize(self, raw=None):
        try:
            self.token = await validate_identity(raw=raw, token=self.token)
            return not self.closed
        except (AuthenticationFailed, TokenError):
            await self.terminate(4401)
        except PermissionDenied:
            await self.terminate(4403)
        except Exception as error:
            logger.warning("WebSocket identity check failed (%s).", type(error).__name__)
            await self.terminate(1013)
        return False

    async def receive(self, text_data=None, bytes_data=None):
        if self.closed:
            return
        if text_data is None:
            await self.terminate(4400)
            return
        if len(text_data.encode("utf-8")) > 8192:
            await self.terminate(1009)
            return
        try:
            message = json.loads(text_data)
        except (ValueError, TypeError):
            await self.terminate(4400)
            return
        if not isinstance(message, dict):
            await self.terminate(4400)
            return
        if not self.authenticated:
            if set(message) != {"type", "access"} or message["type"] != "authenticate" or not isinstance(message["access"], str):
                await self.terminate(4400)
                return
            if not await self.authorize(raw=message["access"]):
                return
            try:
                await asyncio.wait_for(self.channel_layer.group_add(DOCUMENT_GROUP, self.channel_name), timeout=2)
            except Exception as error:
                logger.warning("WebSocket subscription failed (%s).", type(error).__name__)
                await self.terminate(1013)
                return
            if self.closed:
                await self.discard_group()
                return
            if time.time() >= self.token["exp"]:
                await self.terminate(4401)
                return
            self.authenticated = True
            self.auth_timer.cancel()
            self.expiry_timer = asyncio.create_task(self.expire_after(self.token["exp"] - time.time()))
            await self.send(text_data=json.dumps({"type": "authenticated"}))
            return
        if message != {"type": "ping"}:
            await self.terminate(4400)
        elif await self.authorize():
            await self.send(text_data=json.dumps({"type": "pong"}))

    async def document_event(self, event):
        if not self.authenticated or self.closed or not await self.authorize():
            return
        if event.get("event") not in ("document.ready", "document.updated"):
            return
        document_id = event.get("document_id")
        if type(document_id) is not int or document_id <= 0:
            return
        await self.send(text_data=json.dumps({"type": event["event"], "document_id": document_id}))

    async def discard_group(self):
        try:
            await asyncio.wait_for(self.channel_layer.group_discard(DOCUMENT_GROUP, self.channel_name), timeout=2)
        except Exception as error:
            logger.warning("WebSocket subscription cleanup failed (%s).", type(error).__name__)

    async def disconnect(self, close_code):
        if self.cleaned:
            return
        self.cleaned = True
        self.closed = True
        self.authenticated = False
        for timer in (self.auth_timer, self.expiry_timer):
            if timer is not None:
                timer.cancel()
        if hasattr(self, "channel_name"):
            await self.discard_group()
