"""High-priority bypass of normal-priority backlog."""

import db
from models import Device
from util import notify_event, run, seed_event


def test_high_priority_is_claimed_before_normal_backlog(clean_database: None) -> None:
    async def scenario() -> None:
        # A backlog of normal-priority notifications across many recipients (low seqs).
        for index in range(10):
            await seed_event(
                event_id=f"routine-{index}",
                user_id=f"resident-{index}",
                tokens=(f"phone-{index}",),
                priority="normal",
            )
        # A high-priority fall alert arrives LAST (highest seq), for another recipient.
        await seed_event(
            event_id="fall-alert",
            user_id="nurse-on-call",
            tokens=("nurse-phone",),
            priority="high",
        )

        # Claiming a single item must surface the fall alert despite its higher seq -
        # it bypasses the normal backlog.
        claimed = await db.claim_due(batch_size=1, lease_seconds=30)
        assert len(claimed) == 1
        assert claimed[0].event_id == "fall-alert"
        assert claimed[0].priority == "high"

    run(scenario())


def test_priority_does_not_reorder_within_a_recipient(clean_database: None) -> None:
    async def scenario() -> None:
        await db.register_devices(
            [Device(user_id="nurse-on-call", token="nurse-phone", platform="ios")]
        )
        await notify_event(
            event_id="routine-checkup", user_id="nurse-on-call", priority="normal"
        )  # earlier
        await notify_event(
            event_id="fall-alert", user_id="nurse-on-call", priority="high"
        )  # later, high

        # Per-recipient ordering wins over priority: only the earlier (lower-seq)
        # event is claimable; the later high-priority one is gated until it's done.
        claimed = await db.claim_due(batch_size=10, lease_seconds=30)
        assert {delivery.event_id for delivery in claimed} == {"routine-checkup"}

    run(scenario())
