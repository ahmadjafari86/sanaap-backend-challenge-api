import asyncio
import json
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from ..consumers import validate_identity

from config.asgi import build_application


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}, WEBSOCKET_AUTH_TIMEOUT=1, WEBSOCKET_ALLOWED_ORIGINS=["http://localhost:8000"])
class DocumentSocketTests(TransactionTestCase):
    def setUp(self):
        self.application = build_application()
        self.users = {role: get_user_model().objects.create_user(username=f"socket-{role}", role=role) for role in ("admin", "editor", "viewer")}
        self.tokens = {role: str(AccessToken.for_user(user)) for role, user in self.users.items()}
        self.sockets = []

    async def connect(self, *, origin=b"http://localhost:8000", query=b"", extra_headers=()):
        headers = [(b"host", b"localhost:8000"), *extra_headers]
        if origin is not None:
            headers.append((b"origin", origin))
        socket = ApplicationCommunicator(self.application, {"type": "websocket", "path": "/ws/documents/", "raw_path": b"/ws/documents/", "query_string": query, "headers": headers, "scheme": "ws", "subprotocols": []})
        self.sockets.append(socket)
        await socket.send_input({"type": "websocket.connect"})
        return socket, await socket.receive_output()

    async def authenticate(self, socket, token):
        await socket.send_input({"type": "websocket.receive", "text": json.dumps({"type": "authenticate", "access": token})})
        return await socket.receive_output()

    async def disconnect_all(self):
        for socket in self.sockets:
            if not socket.future.done():
                await socket.send_input({"type": "websocket.disconnect", "code": 1000})
            await socket.wait()

    async def event(self, **extra):
        await get_channel_layer().group_send("documents.notifications", {"type": "document.event", "event": "document.ready", "document_id": 23, **extra})

    async def test_all_application_roles_authenticate_with_access_tokens(self):
        try:
            for token in self.tokens.values():
                socket, accepted = await self.connect()
                self.assertEqual(accepted["type"], "websocket.accept")
                response = await self.authenticate(socket, token)
                self.assertEqual(json.loads(response["text"]), {"type": "authenticated"})
        finally:
            await self.disconnect_all()

    async def test_unauthenticated_connection_gets_no_document_events(self):
        try:
            socket, _ = await self.connect()
            await self.event()
            self.assertTrue(await socket.receive_nothing(timeout=0.05))
        finally:
            await self.disconnect_all()

    @override_settings(WEBSOCKET_AUTH_TIMEOUT=0.03)
    async def test_cookie_session_does_not_replace_access_authentication(self):
        try:
            socket, _ = await self.connect(extra_headers=[(b"cookie", b"sessionid=untrusted")])
            self.assertEqual((await socket.receive_output())["code"], 4401)
        finally:
            await self.disconnect_all()

    async def test_missing_or_disallowed_origin_rejects_handshake(self):
        try:
            for origin in (None, b"http://attacker.invalid", b"http://localhost:9999"):
                _, response = await self.connect(origin=origin)
                self.assertEqual(response["type"], "websocket.close")
        finally:
            await self.disconnect_all()

    async def test_query_string_credentials_are_rejected_before_acceptance(self):
        try:
            _, response = await self.connect(query=b"access=private-query-token")
            self.assertEqual(response, {"type": "websocket.close", "code": 4400})
        finally:
            await self.disconnect_all()

    async def test_invalid_expired_and_refresh_tokens_cannot_subscribe(self):
        expired = AccessToken.for_user(self.users["viewer"])
        expired.set_exp(lifetime=timedelta(seconds=-1))
        refresh = RefreshToken()
        refresh["user_id"] = str(self.users["viewer"].pk)
        try:
            for token in ("private-invalid-token", str(expired), str(refresh)):
                socket, _ = await self.connect()
                response = await self.authenticate(socket, token)
                self.assertEqual(response["code"], 4401)
        finally:
            await self.disconnect_all()

    async def test_inactive_deleted_and_unknown_role_users_cannot_subscribe(self):
        try:
            for role, change, code in (("admin", {"is_active": False}, 4401), ("editor", {"role": "unknown"}, 4403)):
                await sync_to_async(get_user_model().objects.filter(pk=self.users[role].pk).update)(**change)
                socket, _ = await self.connect()
                self.assertEqual((await self.authenticate(socket, self.tokens[role]))["code"], code)
            await sync_to_async(self.users["viewer"].delete)()
            socket, _ = await self.connect()
            self.assertEqual((await self.authenticate(socket, self.tokens["viewer"]))["code"], 4401)
        finally:
            await self.disconnect_all()

    async def test_events_contain_only_document_id_and_type(self):
        try:
            socket, _ = await self.connect()
            await self.authenticate(socket, self.tokens["viewer"])
            await self.event(title="private-title", download_url="https://private-token", object_key="private-key")
            response = json.loads((await socket.receive_output())["text"])
            self.assertEqual(response, {"type": "document.ready", "document_id": 23})
        finally:
            await self.disconnect_all()

    async def test_user_deactivation_blocks_the_next_event(self):
        try:
            socket, _ = await self.connect()
            await self.authenticate(socket, self.tokens["viewer"])
            await sync_to_async(get_user_model().objects.filter(pk=self.users["viewer"].pk).update)(is_active=False)
            await self.event()
            self.assertEqual((await socket.receive_output())["code"], 4401)
        finally:
            await self.disconnect_all()

    async def test_role_revocation_blocks_ping_and_further_events(self):
        try:
            socket, _ = await self.connect()
            await self.authenticate(socket, self.tokens["editor"])
            await sync_to_async(get_user_model().objects.filter(pk=self.users["editor"].pk).update)(role="unknown")
            await socket.send_input({"type": "websocket.receive", "text": '{"type":"ping"}'})
            self.assertEqual((await socket.receive_output())["code"], 4403)
        finally:
            await self.disconnect_all()

    async def test_socket_closes_at_access_expiry_even_when_idle(self):
        token = AccessToken.for_user(self.users["viewer"])
        token.set_exp(lifetime=timedelta(seconds=2))
        try:
            socket, _ = await self.connect()
            self.assertEqual(json.loads((await self.authenticate(socket, str(token)))["text"]), {"type": "authenticated"})
            self.assertEqual((await socket.receive_output(timeout=3))["code"], 4401)
        finally:
            await self.disconnect_all()

    async def test_authenticated_ping_returns_pong(self):
        try:
            socket, _ = await self.connect()
            await self.authenticate(socket, self.tokens["admin"])
            await socket.send_input({"type": "websocket.receive", "text": '{"type":"ping"}'})
            self.assertEqual(json.loads((await socket.receive_output())["text"]), {"type": "pong"})
        finally:
            await self.disconnect_all()

    async def test_malformed_binary_and_oversized_frames_close_without_echo(self):
        try:
            for content, code in (({"text": "private-malformed"}, 4400), ({"text": "[]"}, 4400), ({"bytes": b"private-binary"}, 4400), ({"text": "x" * 8193}, 1009)):
                socket, _ = await self.connect()
                await socket.send_input({"type": "websocket.receive", **content})
                self.assertEqual((await socket.receive_output())["code"], code)
        finally:
            await self.disconnect_all()

    async def test_repeated_authentication_cannot_switch_identity(self):
        try:
            socket, _ = await self.connect()
            await self.authenticate(socket, self.tokens["viewer"])
            self.assertEqual((await self.authenticate(socket, self.tokens["admin"]))["code"], 4400)
        finally:
            await self.disconnect_all()

    async def test_database_failure_closes_safely_without_private_exception(self):
        try:
            socket, _ = await self.connect()
            with patch("rest_framework_simplejwt.authentication.JWTAuthentication.get_user", side_effect=OperationalError("private-db-token-url")), self.assertLogs("apps.documents.consumers", level="WARNING") as logs:
                self.assertEqual((await self.authenticate(socket, self.tokens["viewer"]))["code"], 1013)
            self.assertNotIn("private-db-token-url", "\n".join(logs.output))
        finally:
            await self.disconnect_all()

    async def test_layer_failure_cannot_acknowledge_subscription(self):
        try:
            socket, _ = await self.connect()
            with patch.object(get_channel_layer(), "group_add", side_effect=OSError("private-redis-url")), self.assertLogs("apps.documents.consumers", level="WARNING") as logs:
                self.assertEqual((await self.authenticate(socket, self.tokens["viewer"]))["code"], 1013)
            self.assertNotIn("private-redis-url", "\n".join(logs.output))
        finally:
            await self.disconnect_all()

    async def test_layer_receive_failure_closes_safely_and_cleans_up(self):
        failed = asyncio.Event()

        async def receive(*args):
            await failed.wait()
            raise OSError("private-redis-url")

        with patch.object(get_channel_layer(), "receive", side_effect=receive):
            try:
                socket, _ = await self.connect()
                await self.authenticate(socket, self.tokens["viewer"])
                with self.assertLogs("apps.documents.consumers", level="WARNING") as logs:
                    failed.set()
                    self.assertEqual((await socket.receive_output())["code"], 1013)
                    await socket.wait()
                self.assertNotIn("private-redis-url", "\n".join(logs.output))
                self.assertTrue(await socket.receive_nothing(timeout=.02))
            finally:
                await self.disconnect_all()

    def test_identity_recheck_uses_current_time_instead_of_token_creation_time(self):
        # A retained Token caches current_time at construction; rechecks must not.
        token = AccessToken.for_user(self.users["viewer"])
        token.current_time -= timedelta(seconds=10)
        token.set_exp(from_time=token.current_time, lifetime=timedelta(seconds=5))
        with self.assertRaises(TokenError):
            async_to_sync(validate_identity)(token=token)
