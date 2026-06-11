"""Worker happy path.

The worker claims due pending deliveries, pushes each via an injected callable,
and marks 202s delivered. We inject a fake push (no live gateway) so these tests
are deterministic; the real gateway client is wired in worker._main().
"""

import collections

from hypothesis import given, settings
from hypothesis import strategies as st

import db
import worker
from gateway import PushOutcome, PushResult
from models import ClaimedDelivery, Device
from util import notify_event, run, seed_event, truncate


async def always_delivered(delivery: ClaimedDelivery) -> PushResult:
    return PushResult(PushOutcome.DELIVERED)


class RecordingPush:
    """
    A fake push that always succeeds and records how often each pair was pushed.
    """

    def __init__(self) -> None:
        self.calls: collections.Counter[tuple[str, str]] = collections.Counter()
        self.seen: list[ClaimedDelivery] = []

    async def __call__(self, delivery: ClaimedDelivery) -> PushResult:
        self.calls[(delivery.event_id, delivery.token)] += 1
        self.seen.append(delivery)
        return PushResult(PushOutcome.DELIVERED)


def test_delivery_marked_delivered_on_202(clean_database: None) -> None:
    run(
        seed_event(
            event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    processed = run(worker.process_once(always_delivered))
    assert processed == 1
    status = run(db.get_event_status("fall-alert"))
    assert status is not None
    delivery = status.deliveries[0]
    assert delivery.status == "delivered"
    assert delivery.delivered_at is not None


def test_delivered_delivery_is_not_reclaimed(clean_database: None) -> None:
    run(
        seed_event(
            event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    recorder = RecordingPush()
    assert run(worker.process_once(recorder)) == 1
    # The delivered row is terminal, so a second pass claims nothing.
    assert run(worker.process_once(recorder)) == 0
    assert recorder.calls[("fall-alert", "nurse-phone")] == 1


def test_claim_carries_push_payload_fields(clean_database: None) -> None:
    run(
        seed_event(
            event_id="fall-alert",
            user_id="nurse-on-call",
            tokens=("nurse-phone",),
            title="Fall",
            body="Room 14",
        )
    )
    recorder = RecordingPush()
    run(worker.process_once(recorder))
    delivery = recorder.seen[0]
    assert delivery.event_id == "fall-alert"
    assert delivery.token == "nurse-phone"
    assert delivery.platform == "ios"
    assert delivery.title == "Fall"
    assert delivery.body == "Room 14"


async def always_permanent(delivery: ClaimedDelivery) -> PushResult:
    return PushResult(PushOutcome.PERMANENT, detail="http 400")


def test_permanent_failure_is_terminal_without_retry(clean_database: None) -> None:
    run(
        seed_event(
            event_id="bad-payload", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    assert run(worker.process_once(always_permanent)) == 1
    status = run(db.get_event_status("bad-payload"))
    assert status is not None
    assert status.deliveries[0].status == "failed"
    # Terminal: a second pass claims nothing (no retry of a permanent 4xx).
    assert run(worker.process_once(always_permanent)) == 0


def test_requeue_failed_returns_delivery_to_pending(clean_database: None) -> None:
    run(
        seed_event(
            event_id="bad-payload", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    run(worker.process_once(always_permanent))  # -> failed
    assert run(db.requeue_failed("bad-payload")) == 1
    delivery = run(db.get_event_status("bad-payload")).deliveries[0]
    assert delivery.status == "pending"
    assert delivery.attempts == 0
    # Requeued -> claimable and deliverable again.
    assert run(worker.process_once(always_delivered)) == 1
    assert run(db.get_event_status("bad-payload")).deliveries[0].status == "delivered"


identifier = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=5
)


@settings(max_examples=20, deadline=None)
@given(
    tokens=st.lists(identifier, min_size=1, max_size=3, unique=True),
    event_ids=st.lists(identifier, min_size=1, max_size=4, unique=True),
)
def test_drain_delivers_everything_exactly_once(
    tokens: list[str], event_ids: list[str]
) -> None:
    async def scenario() -> None:
        await truncate()
        await db.register_devices(
            [
                Device(user_id="recipient", token=token, platform="ios")
                for token in tokens
            ]
        )
        for event_id in event_ids:
            await notify_event(event_id=event_id, user_id="recipient")

        recorder = RecordingPush()
        while await worker.process_once(recorder, batch_size=5) > 0:  # drain
            pass

        expected_pairs = {
            (event_id, token) for event_id in event_ids for token in tokens
        }
        assert set(recorder.calls) == expected_pairs
        assert all(count == 1 for count in recorder.calls.values())  # exactly once each
        for event_id in event_ids:
            status = await db.get_event_status(event_id)
            assert status is not None
            assert {delivery.status for delivery in status.deliveries} == {"delivered"}

    run(scenario())
