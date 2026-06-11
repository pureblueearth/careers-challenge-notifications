"""
Eval runner for the notifications challenge.

Runs the load harness against your /notify endpoint, then queries the
mock gateway's /_stats and computes a scorecard:

    delivery_rate  : (unique event_id, token pairs with at least one 202) / (unique pairs attempted by gateway)
                     measured against tokens that never became expired during the run
    duplicate_rate : number of (event_id, token) pairs delivered more than once / unique pairs
    ordering       : count of ordering violations per recipient_user_id
    p50/p95/p99    : end-to-end latency from gateway-receive to gateway-delivered

Run (gateway must already be running on :9000):
    python eval/run.py --target http://localhost:8080 baseline

Scenarios are defined below. You can also pass --events / --rps to override.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:9000")


SCENARIOS = {
    "smoke":       {"events": 200,  "rps": 20,  "users": 50},
    "baseline":    {"events": 2000, "rps": 50,  "users": 200},
    "burst":       {"events": 3000, "rps": 300, "users": 300},
    "adversarial": {"events": 3000, "rps": 100, "users": 200},
}


def gateway_request(method: str, path: str) -> dict:
    req = Request(GATEWAY_URL + path, method=method)
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def reset_gateway() -> None:
    try:
        gateway_request("POST", "/_reset")
    except URLError:
        sys.exit(f"Could not reach gateway at {GATEWAY_URL}. "
                 "Start it first with: python mock_gateway/server.py")


def fetch_stats() -> dict:
    return gateway_request("GET", "/_stats")

def score(stats: dict) -> dict:
    attempts = stats.get("attempts", [])
    # (event_id, token) -> list of (status, received_at, delivered_at)
    pairs: dict = {}
    for a in attempts:
        key = (a.get("event_id"), a.get("token"))
        pairs.setdefault(key, []).append(a)

    delivered_pairs = {k for k, hits in pairs.items()
                       if any(h["status"] == 202 for h in hits)}
    duplicate_pairs = {k for k, hits in pairs.items()
                       if sum(1 for h in hits if h["status"] == 202) > 1}


    expired = set(stats.get("expired_tokens", []))
    scored_pairs = {k for k in pairs if k in delivered_pairs or k[1] not in expired}

    latencies = []
    for hits in pairs.values():
        for h in hits:
            if h["status"] == 202 and "delivered_at" in h:
                latencies.append(h["delivered_at"] - h["received_at"])
    latencies.sort()

    def pct(p):
        return latencies[int(len(latencies) * p)] if latencies else 0.0

    return {
        "total_attempts":      len(attempts),
        "unique_pairs":        len(pairs),
        "delivered_pairs":     len(delivered_pairs),
        "duplicate_pairs":     len(duplicate_pairs),
        "delivery_rate":       len(delivered_pairs) / max(len(scored_pairs), 1),
        "duplicate_rate":      len(duplicate_pairs) / max(len(pairs), 1),
        "delivery_p50_ms":     pct(0.5) * 1000,
        "delivery_p95_ms":     pct(0.95) * 1000,
        "delivery_p99_ms":     pct(0.99) * 1000,
    }


def print_scorecard(scenario: str, sc: dict) -> None:
    print(f"\n=== Scorecard: {scenario} ===")
    for k, v in sc.items():
        if isinstance(v, float) and k.startswith(("delivery_rate", "duplicate_rate")):
            print(f"  {k:24s} {v * 100:6.2f}%")
        elif isinstance(v, float):
            print(f"  {k:24s} {v:8.1f}")
        else:
            print(f"  {k:24s} {v}")
    print()
    if sc["delivery_rate"] < 0.99:
        print("  ⚠ delivery rate below 99% - events are being lost or never retried")
    if sc["duplicate_rate"] > 0.0:
        print("  ⚠ duplicates detected - dedup the (event_id, device_token) pair")
    if sc["delivery_p95_ms"] > 5000:
        print("  ⚠ p95 delivery latency over 5s - increase retry concurrency or "
              "tighter backoff")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scenario", choices=list(SCENARIOS))
    p.add_argument("--target", default="http://localhost:8080",
                   help="Your service base URL")
    p.add_argument("--events", type=int)
    p.add_argument("--rps", type=int)
    p.add_argument("--users", type=int)
    args = p.parse_args()

    spec = dict(SCENARIOS[args.scenario])
    if args.events: spec["events"] = args.events
    if args.rps:    spec["rps"] = args.rps
    if args.users:  spec["users"] = args.users

    print(f"Running '{args.scenario}' against {args.target}")
    print(f"  events={spec['events']}  rps={spec['rps']}  users={spec['users']}")
    if args.scenario == "adversarial":
        print("  Reminder: restart the gateway with FAIL_5XX_RATE=0.3 DROP_RATE=0.1")
        print("            for the full adversarial profile.")

    reset_gateway()

    cmd = [
        sys.executable, os.path.join(ROOT, "load_harness", "submit.py"),
        "--target", args.target,
        "--events", str(spec["events"]),
        "--rps", str(spec["rps"]),
        "--users", str(spec["users"]),
    ]
    subprocess.run(cmd, check=False)

    print("\nWaiting 5s for in-flight retries to complete…")
    time.sleep(5)

    stats = fetch_stats()
    print_scorecard(args.scenario, score(stats))


if __name__ == "__main__":
    main()
