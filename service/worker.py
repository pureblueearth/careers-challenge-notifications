"""Delivery worker: claim due deliveries, push each, record the outcome.

Key invariant: the DB connection is not held during the gateway call. claim_due()
returns plain data and commits the lease; the push holds no connection; the result
is a separate short query. That lets in-flight deliveries far outnumber DB
connections.

Run a worker process:  python worker.py
"""

import asyncio
import random
import resource
import signal
from collections.abc import Awaitable, Callable

import httpx

import config
import db
import gateway
import observability
from gateway import PushOutcome, PushResult
from models import ClaimedDelivery

PushCallable = Callable[[ClaimedDelivery], Awaitable[PushResult]]


def backoff_seconds(attempts: int) -> float:
    """
    Full-jitter exponential backoff: a random delay in [0, min(cap, base*2^n)].
    Jitter spreads retries so a burst of failures doesn't all retry in lockstep.
    """
    ceiling = min(
        config.BACKOFF_CAP_SECONDS, config.BACKOFF_BASE_SECONDS * (2**attempts)
    )
    return random.uniform(0.0, ceiling)


async def _handle(delivery: ClaimedDelivery, push: PushCallable) -> None:
    result = await push(delivery)

    match result.outcome:
        case PushOutcome.DELIVERED:
            await db.mark_delivered(delivery.event_id, delivery.token)

            _log(delivery, "delivered")

        case PushOutcome.EXPIRED:
            # 410: token is dead - deactivate it and expire its pending deliveries.
            await db.mark_token_expired(delivery.token)

            _log(delivery, "expired")

        case PushOutcome.RATE_LIMITED:
            # Backpressure, not failure: honor Retry-After, don't count the attempt.
            await db.reschedule(
                delivery.event_id,
                delivery.token,
                delay_seconds=result.retry_after or 1.0,
                error=result.detail,
                count_attempt=False,
            )

            _log(delivery, "rate_limited", retry_after=result.retry_after)

        case PushOutcome.PERMANENT:
            # Client error that won't change on retry (non-410/429 4xx): terminal.
            await db.mark_failed(
                delivery.event_id, delivery.token, error=result.detail
            )

            _log(delivery, "failed", error=result.detail)

        case _:  # TRANSIENT, TIMEOUT, or any future retryable outcome
            next_attempt_number = delivery.attempts + 1
            if next_attempt_number >= config.MAX_DELIVERY_ATTEMPTS:
                await db.mark_failed(
                    delivery.event_id, delivery.token, error=result.detail
                )

                _log(
                    delivery,
                    "failed",
                    attempts=next_attempt_number,
                    error=result.detail,
                )
            else:
                await db.reschedule(
                    delivery.event_id,
                    delivery.token,
                    delay_seconds=backoff_seconds(next_attempt_number),
                    error=result.detail,
                    count_attempt=True,
                )
                
                _log(
                    delivery, "retry", attempts=next_attempt_number, error=result.detail
                )


def _log(delivery: ClaimedDelivery, outcome: str, **extra: object) -> None:
    observability.log(
        "delivery",
        event_id=delivery.event_id,
        token=delivery.token,
        outcome=outcome,
        **extra,
    )
    observability.count(outcome)


async def process_once(
    push: PushCallable,
    batch_size: int = config.CLAIM_BATCH_SIZE,
    lease_seconds: int = config.LEASE_SECONDS,
) -> int:
    """
    Claim one batch of due deliveries and process them concurrently. Returns how
    many were claimed (0 means the queue is currently drained).
    """
    claimed = await db.claim_due(batch_size=batch_size, lease_seconds=lease_seconds)
    if claimed:
        await asyncio.gather(*[_handle(delivery, push) for delivery in claimed])
    return len(claimed)


async def run(
    push: PushCallable,
    *,
    concurrency: int = config.MAX_INFLIGHT,
    batch_size: int = config.CLAIM_BATCH_SIZE,
    lease_seconds: int = config.LEASE_SECONDS,
    poll_interval: float = config.POLL_INTERVAL_SECONDS,
    stop: asyncio.Event,
) -> None:
    """
    Continuously claim due deliveries and push them with a bounded number of
    concurrent in-flight pushes.
    """
    queue: asyncio.Queue[ClaimedDelivery] = asyncio.Queue(maxsize=concurrency)

    async def consumer() -> None:
        while True:
            delivery = await queue.get()
            try:
                await _handle(delivery, push)
            finally:
                queue.task_done()

    consumers: list[asyncio.Task] = [
        asyncio.create_task(consumer()) for _ in range(concurrency)
    ]

    try:
        while not stop.is_set():
            if claimed := await db.claim_due(
                batch_size=batch_size, lease_seconds=lease_seconds
            ):
                for delivery in claimed:
                    await queue.put(delivery)
            else:
                await asyncio.sleep(poll_interval)
    finally:
        await queue.join()  # let already-queued work finish before stopping
        for consumer_task in consumers:
            consumer_task.cancel()


def _raise_fd_limit() -> None:
    """
    Best-effort raise of the open-file-descriptor soft limit; thousands of
    concurrent connections need headroom above the default (often 256).
    """
    try:
        _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 8192), hard))
    except (ImportError, ValueError, OSError):
        pass


async def _main() -> None:
    observability.configure_logging()
    _raise_fd_limit()
    await db.init_pool()
    timeout = httpx.Timeout(config.GATEWAY_TIMEOUT_SECONDS)
    # Allow enough sockets for our in-flight ceiling (httpx defaults to 100).
    limits = httpx.Limits(max_connections=config.MAX_INFLIGHT + 20)

    # Graceful shutdown: SIGINT/SIGTERM set the stop event, so in-flight pushes drain
    # (releasing their leases) instead of dying mid-delivery.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop.set)

    try:
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:

            async def push(delivery: ClaimedDelivery) -> PushResult:
                return await gateway.push(
                    client, config.GATEWAY_URL, delivery, config.CALLBACK_URL
                )

            await run(push, stop=stop)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
