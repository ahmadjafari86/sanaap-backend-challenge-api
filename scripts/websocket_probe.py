"""Container-side client for the disposable integration controller; JSON over stdin."""

import asyncio
import json
import sys

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


async def main():
    sockets = {}

    async def receive(socket, timeout):
        try:
            return {"message": json.loads(await asyncio.wait_for(socket.recv(), timeout))}
        except TimeoutError:
            return {"quiet": True}
        except ConnectionClosed as error:
            return {"closed": error.rcvd.code if error.rcvd else None}

    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            try:
                command = json.loads(line)
                operation, name = command["op"], command.get("name", "viewer")
                if operation == "stop":
                    break
                if operation == "open":
                    try:
                        sockets[name] = await connect("ws://nginx:8080" + command.get("path", "/ws/documents/"), origin=command.get("origin"), proxy=None, open_timeout=5, close_timeout=2, max_size=8192)
                        result = {"connected": True}
                    except InvalidStatus as error:
                        result = {"http_status": error.response.status_code}
                elif operation == "authenticate":
                    await sockets[name].send(json.dumps({"type": "authenticate", "access": command["access"]}))
                    result = await receive(sockets[name], command.get("timeout", 8))
                elif operation == "ping":
                    await sockets[name].send('{"type":"ping"}')
                    result = await receive(sockets[name], 5)
                elif operation == "receive":
                    result = await receive(sockets[name], command.get("timeout", 8))
                elif operation == "close":
                    await sockets.pop(name).close()
                    result = {"closed": True}
                else:
                    raise ValueError("Unknown probe operation")
                print(json.dumps({"ok": True, **result}), flush=True)
            except Exception as error:
                # No credentials, URLs or raw server exception reach controller logs.
                print(json.dumps({"ok": False, "error": type(error).__name__}), flush=True)
    finally:
        await asyncio.gather(*(socket.close() for socket in sockets.values()), return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
