"""Database access layer: owns the asyncpg connection pool, the schema, and all queries."""

from pathlib import Path
from typing import cast

import asyncpg

import config
from models import (
    ClaimedDelivery,
    DeliveryState,
    DeliveryView,
    Device,
    Event,
    EventStatus,
    EventView,
    IntakeResult,
    Platform,
    Priority,
)

# Module-level singleton pool. asyncpg pools are concurrency-safe: many coroutines
# acquire/release connections from the same pool.
_pool: asyncpg.Pool | None = None

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def init_pool() -> asyncpg.Pool:
    """
    Create the pool (once) and apply the schema. Called on app startup.
    """
    global _pool

    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=config.DATABASE_URL,
            min_size=config.DB_POOL_MIN,
            max_size=config.DB_POOL_MAX,
        )
        await _apply_schema()

    return _pool


def pool() -> asyncpg.Pool:
    """
    Accessor used by handlers/queries. Fails loudly if used before init_pool().
    """
    if _pool is None:
        raise RuntimeError("db pool not initialized - call init_pool() first")

    return _pool


async def close_pool() -> None:
    """
    Gracefully drain the pool on shutdown.
    """
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _apply_schema() -> None:
    """
    Run schema.sql. It's idempotent (IF NOT EXISTS), so this is safe every boot.
    """
    schema_sql = _SCHEMA_PATH.read_text()
    async with pool().acquire() as connection:
        await connection.execute(schema_sql)


async def ping() -> None:
    """
    Round-trip to the DB; raises if the database is unreachable.
    """
    async with pool().acquire() as connection:
        await connection.execute("SELECT 1")


# --- devices & intake ----------------------------------------------------


async def register_devices(devices: list[Device]) -> None:
    """
    Batch-upsert devices in one round-trip. Re-registering a token reactivates
    it and updates its owner.
    """
    if not devices:
        return

    async with pool().acquire() as connection:
        await connection.executemany(
            """
            INSERT INTO devices (token, user_id, platform, active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (token) DO UPDATE
                SET user_id = EXCLUDED.user_id,
                    platform = EXCLUDED.platform,
                    active = TRUE
            """,
            [(device.token, device.user_id, device.platform) for device in devices],
        )


async def register_device(device: Device) -> Device:
    """
    Upsert a single device (delegates to the batch path); returns it.
    """
    await register_devices([device])
    return device


async def deactivate_device(token: str) -> Device | None:
    """
    Soft-delete a device (active = FALSE). Returns the deactivated device, or None
    if the token was unknown.
    """
    async with pool().acquire() as connection:
        row = await connection.fetchrow(
            "UPDATE devices SET active = FALSE WHERE token = $1 "
            "RETURNING user_id, token, platform",
            token,
        )

    if row is None:
        return None

    return Device(
        user_id=row["user_id"],
        token=row["token"],
        platform=cast(Platform, row["platform"]),
    )


async def insert_event_and_fanout(event: Event) -> IntakeResult:
    """
    Idempotently record an event and fan out one pending delivery per active
    device, all in one transaction. A re-submitted event_id is a no-op (no extra
    deliveries).
    """
    async with pool().acquire() as connection:
        async with connection.transaction():
            inserted = await connection.fetchrow(
                """
                INSERT INTO events (event_id, user_id, title, body, priority, occurred_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING seq
                """,
                event.event_id,
                event.user_id,
                event.title,
                event.body,
                event.priority,
                event.occurred_at,
            )
            if inserted is None:
                # Duplicate event_id! already accepted; do not fan out again.
                return IntakeResult(
                    event_id=event.event_id, created=False, delivery_count=0
                )

            sequence: int = inserted["seq"]

            devices = await connection.fetch(
                "SELECT token FROM devices WHERE user_id = $1 AND active", event.user_id
            )

            if devices:
                await connection.executemany(
                    """
                    INSERT INTO deliveries (event_id, token, user_id, seq, priority)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (event_id, token) DO NOTHING
                    """,
                    [
                        (
                            event.event_id,
                            device["token"],
                            event.user_id,
                            sequence,
                            event.priority,
                        )
                        for device in devices
                    ],
                )

            return IntakeResult(
                event_id=event.event_id, created=True, delivery_count=len(devices)
            )


async def get_event_status(event_id: str) -> EventStatus | None:
    """
    The event plus the status of every one of its deliveries. None if unknown.
    """
    async with pool().acquire() as connection:
        if not (
            event := await connection.fetchrow(
                """
                SELECT event_id, user_id, title, body, priority, occurred_at, seq, received_at
                FROM events WHERE event_id = $1
                """,
                event_id,
            )
        ):
            return None

        deliveries = await connection.fetch(
            """
            SELECT token, status, attempts, next_attempt_at, delivered_at, last_error
            FROM deliveries
            WHERE event_id = $1
            ORDER BY token
            """,
            event_id,
        )

    return EventStatus(
        event=EventView(
            event_id=event["event_id"],
            user_id=event["user_id"],
            title=event["title"],
            body=event["body"],
            priority=cast(Priority, event["priority"]),
            occurred_at=event["occurred_at"],
            seq=event["seq"],
            received_at=event["received_at"],
        ),
        deliveries=[
            DeliveryView(
                token=delivery["token"],
                status=cast(DeliveryState, delivery["status"]),
                attempts=delivery["attempts"],
                next_attempt_at=delivery["next_attempt_at"],
                delivered_at=delivery["delivered_at"],
                last_error=delivery["last_error"],
            )
            for delivery in deliveries
        ],
    )


# --- delivery ------------------------------------------------------------


async def claim_due(batch_size: int, lease_seconds: int) -> list[ClaimedDelivery]:
    """
    Atomically claim up to `batch_size` due pending deliveries - skipping rows
    another worker holds (SKIP LOCKED), respecting per-user order, stamping a
    lease, and returning everything needed to build a push.

    One statement, so row locks last only for its duration and the lease lives in
    the data. Keeping the connection free during the gateway call that follows.
    """
    rows = await pool().fetch(
        """
        WITH claimable AS (
            SELECT event_id, token
            FROM deliveries
            WHERE status = 'pending'
              AND next_attempt_at <= now()
              AND (leased_until IS NULL OR leased_until < now())
              -- Ordering gate: skip while the same user has a lower-seq row still
              -- pending (a leased, in-flight row stays pending, so it keeps blocking).
              -- A terminal predecessor (expired/failed) deliberately does NOT block,
              -- so a dead or 'poisoned' delivery can't wedge the recipient (liveness wins).
              AND NOT EXISTS (
                  SELECT 1 FROM deliveries AS earlier
                  WHERE earlier.user_id = deliveries.user_id
                    AND earlier.seq < deliveries.seq
                    AND earlier.status = 'pending'
              )
            -- Priority bypass across users; the gate above still enforces per-user order.
            ORDER BY (priority = 'high') DESC, seq
            LIMIT $1
            FOR UPDATE SKIP LOCKED
        ),
        claimed AS (
            UPDATE deliveries AS d
            SET leased_until = now() + make_interval(secs => $2)
            FROM claimable AS c
            WHERE d.event_id = c.event_id AND d.token = c.token
            RETURNING d.event_id, d.token, d.user_id, d.seq, d.priority, d.attempts
        )
        SELECT claimed.event_id, claimed.token, claimed.user_id, claimed.seq,
               claimed.priority, claimed.attempts,
               e.title, e.body, dev.platform
        FROM claimed
        JOIN events  AS e   ON e.event_id = claimed.event_id
        JOIN devices AS dev ON dev.token  = claimed.token
        """,
        batch_size,
        lease_seconds,
    )
    return [
        ClaimedDelivery(
            event_id=row["event_id"],
            token=row["token"],
            user_id=row["user_id"],
            seq=row["seq"],
            platform=cast(Platform, row["platform"]),
            title=row["title"],
            body=row["body"],
            priority=cast(Priority, row["priority"]),
            attempts=row["attempts"],
        )
        for row in rows
    ]


async def mark_delivered(event_id: str, token: str) -> None:
    """
    Terminal success: a 202 from the gateway. Guarded on status = 'pending' (like
    the callback path) so a re-claim race cannot resurrect a row another worker has
    already moved to a terminal state.
    """
    await pool().execute(
        """
        UPDATE deliveries
        SET status = 'delivered', delivered_at = now(), leased_until = NULL
        WHERE event_id = $1 AND token = $2 AND status = 'pending'
        """,
        event_id,
        token,
    )


async def reschedule(
    event_id: str,
    token: str,
    delay_seconds: float,
    error: str | None,
    count_attempt: bool,
) -> None:
    """
    Retry later: push next_attempt_at out and release the lease. `count_attempt`
    controls whether this counts toward the give-up cap. True for genuine failures
    (5xx/timeout), False for 429 backpressure (rate limiting must not cause a drop).
    """
    await pool().execute(
        """
        UPDATE deliveries
        SET next_attempt_at = now() + make_interval(secs => $3),
            leased_until = NULL,
            attempts = attempts + $4::int,
            last_error = $5
        WHERE event_id = $1 AND token = $2
        """,
        event_id,
        token,
        delay_seconds,
        1 if count_attempt else 0,
        error,
    )


async def mark_token_expired(token: str) -> None:
    """
    410 Gone: the token is permanently dead. Deactivate it (so future events skip
    it) and expire every still-pending delivery to it (no point retrying).
    """
    async with pool().acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE devices SET active = FALSE WHERE token = $1", token
            )
            await connection.execute(
                """
                UPDATE deliveries
                SET status = 'expired', leased_until = NULL
                WHERE token = $1 AND status = 'pending'
                """,
                token,
            )


async def mark_delivered_via_callback(event_id: str, token: str) -> None:
    """
    Mark delivered from the gateway's out-of-band confirmation. Guarded on pending,
    so it closes the worker-crash window without disturbing the normal path.
    """
    await pool().execute(
        """
        UPDATE deliveries
        SET status = 'delivered', delivered_at = now(), leased_until = NULL
        WHERE event_id = $1 AND token = $2 AND status = 'pending'
        """,
        event_id,
        token,
    )


async def mark_failed(event_id: str, token: str, error: str | None) -> None:
    """
    Terminal failure: gave up after the attempt cap. Surfaced via observability.
    """
    await pool().execute(
        """
        UPDATE deliveries
        SET status = 'failed', leased_until = NULL, attempts = attempts + 1, last_error = $3
        WHERE event_id = $1 AND token = $2
        """,
        event_id,
        token,
        error,
    )


async def requeue_failed(event_id: str | None = None) -> int:
    """
    Re-drive failed deliveries back to pending for a fresh attempt cycle (manual
    recovery). Scoped to one event when event_id is given, else all. Returns the
    count. A requeued row re-enters the gate at its original seq, so a re-drive can
    deliver out of order vs events already sent - acceptable for manual recovery.
    """
    status = await pool().execute(
        """
        UPDATE deliveries
        SET status = 'pending', next_attempt_at = now(), attempts = 0,
            leased_until = NULL, last_error = NULL
        WHERE status = 'failed' AND ($1::text IS NULL OR event_id = $1)
        """,
        event_id,
    )
    return int(status.split()[-1])  # asyncpg returns a tag like "UPDATE 3"


async def delivery_status_counts() -> dict[str, int]:
    """
    Count deliveries by status across the whole table. This is the cross-process
    source of truth for delivery outcomes: the workers record them in separate
    processes, so their in-memory counters never reach the API's /metrics.
    """
    rows = await pool().fetch(
        "SELECT status, count(*) AS total FROM deliveries GROUP BY status"
    )
    counts = {
        f"deliveries_{state}": 0
        for state in ("pending", "delivered", "expired", "failed")
    }
    counts.update({f"deliveries_{row['status']}": row["total"] for row in rows})
    return counts
