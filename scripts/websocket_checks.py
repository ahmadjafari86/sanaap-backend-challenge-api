"""Host controller uses only stdlib; the probe's client library lives in the image."""

import json
import select
import subprocess


class SocketChecks:
    def __init__(self, compose, origin):
        self.origin = origin
        self.process = subprocess.Popen(compose + ["exec", "-T", "backend", "python", "scripts/websocket_probe.py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def command(self, operation, **values):
        self.process.stdin.write(json.dumps({"op": operation, **values}) + "\n")
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 20)[0]:
            raise RuntimeError("WebSocket probe timed out.")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("WebSocket probe exited without a result.")
        result = json.loads(line)
        if not result.pop("ok", False):
            raise RuntimeError(f"WebSocket probe failed ({result.get('error', 'unknown')}).")
        return result

    def open(self, name, access):
        assert self.command("open", name=name, origin=self.origin) == {"connected": True}
        assert self.command("authenticate", name=name, access=access) == {"message": {"type": "authenticated"}}

    def event(self, name, event, document_id):
        assert self.command("receive", name=name) == {"message": {"type": event, "document_id": document_id}}

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.communicate('{"op":"stop"}\n', timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate(timeout=5)
