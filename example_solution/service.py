"""
Example *stub* solution to show the API contract.

This is not a real solution — it accepts /notify and immediately POSTs once
to the gateway, with no retries, no dedup, no ordering, no persistence. It
will score badly on every scenario in eval/run.py. Use it as a starting
point for your own implementation.

Run:
    python example_solution/service.py
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


PORT = int(os.environ.get("PORT", "8080"))
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:9000")


# (toy) user_id -> [device_tokens] registry
_devices: dict = {}
_lock = threading.Lock()


def push_to_gateway(event_id: str, token: str, title: str, body: str) -> int:
    payload = json.dumps({
        "device_token": token,
        "event_id": event_id,
        "title": title,
        "body": body,
        "platform": "ios",
    }).encode()
    req = Request(GATEWAY_URL + "/push", data=payload, method="POST",
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as resp:
            resp.read()
            return resp.status
    except HTTPError as e:
        return e.code
    except (URLError, OSError):
        return 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - shadows builtin; matches base signature
        pass

    def do_POST(self):
        if self.path == "/notify":
            self._handle_notify()
        elif self.path == "/devices":
            self._handle_device_register()
        else:
            self._send(404, {"error": "unknown path"})

    def _handle_notify(self):
        body = self._read_json()
        if not body:
            self._send(400, {"error": "invalid json"})
            return

        user = body.get("recipient_user_id")
        with _lock:
            tokens = list(_devices.get(user, []))

        # No registered devices? Auto-register a fake one so the demo works.
        if not tokens:
            token = f"tok_{user}_default"
            with _lock:
                _devices.setdefault(user, []).append(token)
            tokens = [token]

        for token in tokens:
            push_to_gateway(
                body.get("event_id"), token,
                body.get("title", ""), body.get("body", ""),
            )

        self._send(202, {"queued": True})

    def _handle_device_register(self):
        body = self._read_json()
        if not body:
            self._send(400, {"error": "invalid json"})
            return
        with _lock:
            _devices.setdefault(body["user_id"], []).append(body["token"])
        self._send(201, {"ok": True})

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return None

    def _send(self, status: int, body: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


def main():
    print(f"Example stub service listening on :{PORT}; gateway at {GATEWAY_URL}")
    print("This is a deliberately bad reference implementation. Beat it.")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
