"""Central configuration, read from the environment (.env loaded on import)."""

import os

from dotenv import load_dotenv

# Best-effort .env load; a no-op when the env is set directly (e.g. production).
load_dotenv()

# --- connections ---------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "postgres://localhost:5432/notifications")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:9000")
PORT = int(os.environ.get("PORT", "8080"))
# Where the gateway POSTs delivery confirmations. The crash-safety net: a confirmed
# delivery goes terminal without the worker, so a stranded in-flight row is never re-sent.
CALLBACK_URL = os.environ.get("CALLBACK_URL", "http://localhost:8080/_callback")

# --- database pool -------------------------------------------------------
# The API holds connections only briefly, so a small pool suffices; workers pool separately.
DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))

# --- worker knobs --------------------------------------------------------
GATEWAY_TIMEOUT_SECONDS = float(os.environ.get("GATEWAY_TIMEOUT_SECONDS", "6.0"))
LEASE_SECONDS = int(os.environ.get("LEASE_SECONDS", "120"))
CLAIM_BATCH_SIZE = int(os.environ.get("CLAIM_BATCH_SIZE", "50"))
POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "0.1"))
# Capped to bound file descriptors.
MAX_INFLIGHT = int(os.environ.get("MAX_INFLIGHT", "256"))

# --- retry policy --------------------------------------------------------
# Generous cap: exhausting this many attempts is near-impossible unless a delivery is
# truly dead. The liveness escape that stops a poison token wedging a recipient.
MAX_DELIVERY_ATTEMPTS = int(os.environ.get("MAX_DELIVERY_ATTEMPTS", "10"))
# Full-jitter exponential backoff: delay in [0, min(cap, base * 2**attempts)]. Tight
# because a retry head-of-line-blocks the recipient's later events, and gateway faults
# are short-lived (429 Retry-After=1s, brief 5xx).
BACKOFF_BASE_SECONDS = float(os.environ.get("BACKOFF_BASE_SECONDS", "0.25"))
BACKOFF_CAP_SECONDS = float(os.environ.get("BACKOFF_CAP_SECONDS", "3.0"))
