"""Per-recipient ordering.

Invariant: for a given recipient, the earlier event (lower seq) is delivered to
all its devices before a later event (higher seq) is attempted at all. Enforced
by the claim predicate 'no lower-sequence pending delivery exists for this recipient'.
"""

import collections

from hypothesis import given, settings
from hypothesis import strategies as st

import db
import worker
from gateway import PushOutcome, PushResult
from models import ClaimedDelivery, Device
from util import notify_event, run, truncate


class OrderRecorder:
    """
    A fake push that always succeeds and records, per recipient, the seq order in
    which deliveries were attempted.
    """

    def __init__(self) -> None:
        self.per_recipient: dict[str, list[int]] = collections.defaultdict(list)

    async def __call__(self, delivery: ClaimedDelivery) -> PushResult:
        self.per_recipient[delivery.user_id].append(delivery.seq)
        return PushResult(PushOutcome.DELIVERED)


def test_claim_never_skips_ahead_of_a_pending_lower_seq(clean_database: None) -> None:
    async def scenario() -> None:
        await db.register_devices(
            [
                Device(user_id="recipient", token=f"phone-{index}", platform="ios")
                for index in range(2)
            ]
        )
        for index in range(3):  # three events, increasing seq, all pending
            await notify_event(event_id=f"alert-{index}", user_id="recipient")

        claimed = await db.claim_due(batch_size=100, lease_seconds=30)

        # No claimed delivery may have a still-pending lower-seq sibling for its recipient.
        for delivery in claimed:
            has_earlier_pending = await db.pool().fetchval(
                "SELECT EXISTS(SELECT 1 FROM deliveries "
                "WHERE user_id = $1 AND seq < $2 AND status = 'pending')",
                delivery.user_id,
                delivery.seq,
            )
            assert not has_earlier_pending

        # Therefore only the recipient's lowest event (one seq) is claimable at once.
        assert len({delivery.seq for delivery in claimed}) == 1

    run(scenario())


@settings(max_examples=25, deadline=None)
@given(
    recipient_count=st.integers(min_value=1, max_value=3),
    events_per_recipient=st.integers(min_value=1, max_value=4),
    devices_per_recipient=st.integers(min_value=1, max_value=2),
    batch_size=st.integers(min_value=1, max_value=5),
)
def test_per_recipient_delivery_order_is_monotonic(
    recipient_count: int,
    events_per_recipient: int,
    devices_per_recipient: int,
    batch_size: int,
) -> None:
    async def scenario() -> None:
        await truncate()
        for recipient_index in range(recipient_count):
            user_id = f"recipient-{recipient_index}"
            await db.register_devices(
                [
                    Device(
                        user_id=user_id,
                        token=f"recipient-{recipient_index}-phone-{device_index}",
                        platform="ios",
                    )
                    for device_index in range(devices_per_recipient)
                ]
            )
            for event_index in range(
                events_per_recipient
            ):  # increasing seq per recipient
                await notify_event(
                    event_id=f"alert-{recipient_index}-{event_index}", user_id=user_id
                )

        recorder = OrderRecorder()
        for _ in range(1000):  # drain (always-success push, no backoff)
            if await worker.process_once(recorder, batch_size=batch_size) == 0:
                break

        for user_id, seqs in recorder.per_recipient.items():
            assert seqs == sorted(seqs), f"ordering violated for {user_id}: {seqs}"

    run(scenario())
