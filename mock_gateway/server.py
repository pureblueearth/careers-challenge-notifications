"""
Mock push gateway for the Teton notifications challenge.

Simulates how real push providers (APNs, FCM, web push) behave under load
and partial failure. Your service POSTs to /push; this server returns 202
(accepted), 410 (token expired), 429 (rate limited), 500/503 (transient),
or occasionally just hangs and doesn't respond at all.

Run:
    python mock_gateway/server.py

Tunable via env vars (all optional):
    PORT=9000                       listen port
    FAIL_5XX_RATE=0.05              probability of returning 5xx
    RATE_LIMIT_RPS_PER_TOKEN=5      per-token sliding-1s rate limit
    TOKEN_EXPIRY_RATE=0.02          probability a given token gets marked expired
    DROP_RATE=0.01                  probability the gateway drops a request silently
    LATENCY_MAX_MS=4000             upper bound on simulated tail latency
    CALLBACK_DELAY_MS=200           delay before delivery callback fires
"""

import collections
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import URLError


PORT = int(os.environ.get("PORT", "9000"))
FAIL_5XX_RATE = float(os.environ.get("FAIL_5XX_RATE", "0.05"))
RATE_LIMIT_RPS_PER_TOKEN = int(os.environ.get("RATE_LIMIT_RPS_PER_TOKEN", "5"))
TOKEN_EXPIRY_RATE = float(os.environ.get("TOKEN_EXPIRY_RATE", "0.02"))
DROP_RATE = float(os.environ.get("DROP_RATE", "0.01"))
LATENCY_MAX_MS = int(os.environ.get("LATENCY_MAX_MS", "4000"))
CALLBACK_DELAY_MS = int(os.environ.get("CALLBACK_DELAY_MS", "200"))


_lock = threading.Lock()
_expired_tokens: set = set()
_rate_buckets: dict = collections.defaultdict(collections.deque)
# Append-only log of every push attempt. Used by the eval to compute
# delivery rate, duplicate rate, and ordering.
_attempts: list = []


def _is_expired(token: str) -> bool:
    """Mark a token expired (sticky) with TOKEN_EXPIRY_RATE on first sight."""
    with _lock:
        if token in _expired_tokens:
            return True
        if random.random() < TOKEN_EXPIRY_RATE:
            _expired_tokens.add(token)
            return True
        return False


def _over_rate_limit(token: str) -> bool:
    """Sliding 1-second per-token rate limit."""
    now = time.monotonic()
    with _lock:
        bucket = _rate_buckets[token]
        while bucket and now - bucket[0] > 1.0:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_RPS_PER_TOKEN:
            return True
        bucket.append(now)
        return False


def _record(attempt: dict) -> None:
    with _lock:
        _attempts.append(attempt)


def _fire_callback(url: str, payload: dict) -> None:
    """POST payload to url after CALLBACK_DELAY_MS, fire-and-forget."""
    def go():
        time.sleep(CALLBACK_DELAY_MS / 1000)
        try:
            req = Request(
                url,
                data=json.dumps(payload).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urlopen(req, timeout=5).read()
        except (URLError, ValueError, OSError):
            pass

    threading.Thread(target=go, daemon=True).start()


class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - shadows builtin; matches base signature
        pass  # silence default access logging

    def do_POST(self):
        if self.path == "/push":
            self._handle_push()
            return
        if self.path == "/_reset":
            with _lock:
                _expired_tokens.clear()
                _rate_buckets.clear()
                _attempts.clear()
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "unknown path"})

    def do_GET(self):
        if self.path == "/_stats":
            with _lock:
                self._send(200, {
                    "attempts": list(_attempts),
                    "expired_tokens": list(_expired_tokens),
                })
            return
        if self.path == "/_health":
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "unknown path"})

    def _handle_push(self) -> None:
        body = self._read_json()
        if body is None:
            self._send(400, {"error": "invalid json"})
            return

        token = body.get("device_token")
        if not token:
            self._send(400, {"error": "device_token required"})
            return

        event_id = body.get("event_id")
        received_at = time.time()

        # Tail latency
        time.sleep(random.uniform(0, LATENCY_MAX_MS) / 1000)

        # Drop entirely: hold past the candidate's reasonable timeout
        if random.random() < DROP_RATE:
            _record({"event_id": event_id, "token": token, "status": "dropped",
                     "received_at": received_at})
            time.sleep(31)
            return

        if _is_expired(token):
            _record({"event_id": event_id, "token": token, "status": 410,
                     "received_at": received_at})
            self._send(410, {"error": "token expired"})
            return

        if _over_rate_limit(token):
            _record({"event_id": event_id, "token": token, "status": 429,
                     "received_at": received_at})
            self.send_response(429)
            self.send_header("Retry-After", "1")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"rate limited"}')
            return

        if random.random() < FAIL_5XX_RATE:
            status = random.choice([500, 503])
            _record({"event_id": event_id, "token": token, "status": status,
                     "received_at": received_at})
            self._send(status, {"error": "transient"})
            return

        # Accepted
        _record({
            "event_id": event_id,
            "token": token,
            "status": 202,
            "received_at": received_at,
            "delivered_at": received_at + CALLBACK_DELAY_MS / 1000,
        })
        callback = body.get("callback_url")
        if callback:
            _fire_callback(callback, {
                "event_id": event_id,
                "device_token": token,
                "delivered_at": time.time(),
                "status": "delivered",
            })
        self._send(202, {
            "message_id": event_id or f"msg_{int(received_at * 1000)}",
            "status": "queued",
        })

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
    print(f"Mock push gateway listening on :{PORT}")
    print(f"  FAIL_5XX_RATE={FAIL_5XX_RATE}")
    print(f"  RATE_LIMIT_RPS_PER_TOKEN={RATE_LIMIT_RPS_PER_TOKEN}")
    print(f"  TOKEN_EXPIRY_RATE={TOKEN_EXPIRY_RATE}")
    print(f"  DROP_RATE={DROP_RATE}")
    print(f"  LATENCY_MAX_MS={LATENCY_MAX_MS}")
    print(f"  CALLBACK_DELAY_MS={CALLBACK_DELAY_MS}")
    print()
    print("Endpoints:")
    print("  POST /push       <- your service calls this; see README for shape")
    print("  GET  /_stats     attempts log + expired tokens (used by eval)")
    print("  POST /_reset     clear state, fresh run")
    print("  GET  /_health    liveness check")
    ThreadingHTTPServer(("0.0.0.0", PORT), GatewayHandler).serve_forever()


if __name__ == "__main__":
    main()
