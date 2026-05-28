"""
Load harness for the notifications challenge.

Submits N events to your /notify endpoint at a steady RPS, then waits and
prints a summary of HTTP responses and request latency. This does not
score correctness end-to-end — eval/run.py does that by querying the mock
gateway's /_stats endpoint.

Run:
    python load_harness/submit.py --target http://localhost:8080 --events 1000 --rps 50
"""

import argparse
import json
import random
import threading
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


def submit(target: str, event: dict) -> tuple:
    """POST event to target/notify; return (status, latency_seconds)."""
    started = time.monotonic()
    req = Request(
        target.rstrip("/") + "/notify",
        data=json.dumps(event).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=10) as resp:
            resp.read()
            return resp.status, time.monotonic() - started
    except HTTPError as e:
        return e.code, time.monotonic() - started
    except (URLError, OSError):
        return 0, time.monotonic() - started


def build_event(user_id: str, priority: str) -> dict:
    eid = f"evt_{uuid.uuid4().hex[:12]}"
    return {
        "event_id": eid,
        "recipient_user_id": user_id,
        "title": f"Test notification {eid[:8]}",
        "body": "If you can read this, the candidate's service worked",
        "priority": priority,
        "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="http://localhost:8080",
                   help="Your service base URL (default: http://localhost:8080)")
    p.add_argument("--events", type=int, default=1000)
    p.add_argument("--users", type=int, default=500)
    p.add_argument("--rps", type=int, default=100)
    p.add_argument("--high-priority-rate", type=float, default=0.05)
    p.add_argument("--retry-duplicate-rate", type=float, default=0.05,
                   help="Probability we re-submit the same event_id (testing your dedup)")
    args = p.parse_args()

    interval = 1.0 / max(args.rps, 1)
    statuses: list = []
    lock = threading.Lock()

    def worker(event: dict):
        status, latency = submit(args.target, event)
        with lock:
            statuses.append((status, latency))

    print(f"Submitting {args.events} events to {args.target} at ~{args.rps} rps")
    started = time.monotonic()
    threads = []
    submitted = []
    for _ in range(args.events):
        user = f"user_{random.randint(1, args.users):04d}"
        priority = "high" if random.random() < args.high_priority_rate else "normal"
        event = build_event(user, priority)
        submitted.append(event)
        t = threading.Thread(target=worker, args=(event,), daemon=True)
        t.start()
        threads.append(t)
        # Re-submit some events to test dedup
        if random.random() < args.retry_duplicate_rate:
            t2 = threading.Thread(target=worker, args=(event,), daemon=True)
            t2.start()
            threads.append(t2)
        time.sleep(interval)
    for t in threads:
        t.join(timeout=20)
    elapsed = time.monotonic() - started

    accepted = sum(1 for s, _ in statuses if 200 <= s < 300)
    rejected = sum(1 for s, _ in statuses if s >= 400)
    errored = sum(1 for s, _ in statuses if s == 0)
    latencies = sorted(l for _, l in statuses)
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

    print(f"\nresults after {elapsed:.1f}s:")
    print(f"  submitted (incl. retries): {len(statuses)}")
    print(f"  unique events:             {len(submitted)}")
    print(f"  accepted (2xx):            {accepted}")
    print(f"  rejected (4xx+):           {rejected}")
    print(f"  errored (no resp):         {errored}")
    print(f"  /notify latency:           "
          f"p50={p50 * 1000:.0f}ms  p95={p95 * 1000:.0f}ms  p99={p99 * 1000:.0f}ms")


if __name__ == "__main__":
    main()
