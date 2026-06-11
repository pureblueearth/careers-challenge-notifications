"""Bounded concurrency and multi-worker safety."""

import asyncio
import collections

import db
import worker
from gateway import PushOutcome, PushResult
from models import ClaimedDelivery, Device
from util import notify_event, run


class ConcurrencyProbe:
    """
    Always-success push that tracks the peak number of simultaneous pushes.
    """

    def __init__(self) -> None:
        self.current = 0
        self.max_seen = 0
        self.calls: collections.Counter[tuple[str, str]] = collections.Counter()

    async def __call__(self, delivery: ClaimedDelivery) -> PushResult:
        self.current += 1
        self.max_seen = max(self.max_seen, self.current)
        self.calls[(delivery.event_id, delivery.token)] += 1
        await asyncio.sleep(0.01)  # hold the "connection" so pushes overlap
        self.current -= 1
        return PushResult(PushOutcome.DELIVERED)


async def _seed_many(recipients: int, devices: int, events: int) -> None:
    for recipient_index in range(recipients):
        user_id = f"recipient-{recipient_index}"
        await db.register_devices(
            [
                Device(
                    user_id=user_id,
                    token=f"recipient-{recipient_index}-phone-{device_index}",
                    platform="ios",
                )
                for device_index in range(devices)
            ]
        )
        for event_index in range(events):
            await notify_event(
                event_id=f"alert-{recipient_index}-{event_index}", user_id=user_id
            )


async def _drain_pending(max_ticks: int = 4000) -> None:
    for _ in range(max_ticks):
        pending = await db.pool().fetchval(
            "SELECT count(*) FROM deliveries WHERE status = 'pending'"
        )
        if pending == 0:
            return
        await asyncio.sleep(0.005)

    raise AssertionError("queue did not drain in time")


def test_inflight_never_exceeds_concurrency(clean_database: None) -> None:
    async def scenario() -> None:
        await _seed_many(recipients=6, devices=2, events=3)  # 36 deliveries
        probe = ConcurrencyProbe()
        stop = asyncio.Event()
        worker_task = asyncio.create_task(
            worker.run(
                probe, concurrency=4, batch_size=8, poll_interval=0.005, stop=stop
            )
        )
        await _drain_pending()
        stop.set()
        await worker_task

        assert probe.max_seen <= 4  # the hard ceiling
        assert probe.max_seen >= 2  # genuinely overlapped, not serial
        assert all(count == 1 for count in probe.calls.values())  # each pushed once
        remaining = await db.pool().fetchval(
            "SELECT count(*) FROM deliveries WHERE status <> 'delivered'"
        )
        assert remaining == 0

    run(scenario())


def test_two_workers_share_queue_without_double_delivering(
    clean_database: None,
) -> None:
    async def scenario() -> None:
        await _seed_many(recipients=5, devices=2, events=3)  # 30 deliveries
        probe = ConcurrencyProbe()
        stop = asyncio.Event()
        workers = [
            asyncio.create_task(
                worker.run(
                    probe, concurrency=3, batch_size=5, poll_interval=0.005, stop=stop
                )
            )
            for _ in range(2)
        ]
        await _drain_pending()
        stop.set()
        await asyncio.gather(*workers)

        assert all(
            count == 1 for count in probe.calls.values()
        )  # SKIP LOCKED: no double-claim
        assert sum(probe.calls.values()) == 5 * 2 * 3  # all delivered exactly once
        assert probe.max_seen <= 6  # combined ceiling across both workers

    run(scenario())
