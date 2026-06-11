"""How the worker reacts to each gateway outcome.

410 -> expired + token deactivated (and siblings expired);
429 -> reschedule without burning the attempt budget;
5xx/timeout -> backoff + attempt++; attempts cap -> failed;
terminal states are never reclaimed.
"""

from collections.abc import Awaitable, Callable

from hypothesis import given, settings
from hypothesis import strategies as st

import config
import db
import worker
from gateway import PushOutcome, PushResult
from models import ClaimedDelivery
from util import notify_event, run, seed_event

PushCallable = Callable[[ClaimedDelivery], Awaitable[PushResult]]


def fixed_push(result: PushResult) -> PushCallable:
    """
    A fake push that always returns the same outcome.
    """

    async def _push(delivery: ClaimedDelivery) -> PushResult:
        return result

    return _push


async def _force_attempts(event_id: str, token: str, attempts: int) -> None:
    await db.pool().execute(
        "UPDATE deliveries SET attempts = $3 WHERE event_id = $1 AND token = $2",
        event_id,
        token,
        attempts,
    )


def test_expired_token_marks_delivery_expired_and_deactivates(
    clean_database: None,
) -> None:
    run(
        seed_event(
            event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    run(worker.process_once(fixed_push(PushResult(PushOutcome.EXPIRED))))

    status = run(db.get_event_status("fall-alert"))
    assert status is not None
    assert status.deliveries[0].status == "expired"

    # Token deactivated -> a later event for the same recipient does not fan out to it.
    run(notify_event(event_id="second-alert", user_id="nurse-on-call"))
    later = run(db.get_event_status("second-alert"))
    assert later is not None
    assert later.deliveries == []


def test_expired_token_bulk_expires_pending_siblings(clean_database: None) -> None:
    run(
        seed_event(
            event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    run(
        notify_event(event_id="second-alert", user_id="nurse-on-call")
    )  # second pending delivery, same token

    # Claim just one; the 410 should expire all pending deliveries for that token.
    run(worker.process_once(fixed_push(PushResult(PushOutcome.EXPIRED)), batch_size=1))

    first = run(db.get_event_status("fall-alert"))
    second = run(db.get_event_status("second-alert"))
    assert first is not None and second is not None
    assert first.deliveries[0].status == "expired"
    assert second.deliveries[0].status == "expired"


def test_rate_limited_reschedules_without_counting_attempt(
    clean_database: None,
) -> None:
    run(
        seed_event(
            event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    run(
        worker.process_once(
            fixed_push(PushResult(PushOutcome.RATE_LIMITED, retry_after=1.0))
        )
    )

    status = run(db.get_event_status("fall-alert"))
    assert status is not None
    delivery = status.deliveries[0]
    assert delivery.status == "pending"
    assert delivery.attempts == 0  # backpressure, not a failure


def test_transient_failure_backs_off_and_counts_attempt(clean_database: None) -> None:
    run(
        seed_event(
            event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    run(
        worker.process_once(
            fixed_push(PushResult(PushOutcome.TRANSIENT, detail="boom"))
        )
    )

    status = run(db.get_event_status("fall-alert"))
    assert status is not None
    delivery = status.deliveries[0]
    assert delivery.status == "pending"
    assert delivery.attempts == 1
    assert delivery.last_error == "boom"


def test_gives_up_after_max_attempts(clean_database: None) -> None:
    run(
        seed_event(
            event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    run(_force_attempts("fall-alert", "nurse-phone", config.MAX_DELIVERY_ATTEMPTS - 1))
    run(
        worker.process_once(
            fixed_push(PushResult(PushOutcome.TRANSIENT, detail="dead"))
        )
    )

    status = run(db.get_event_status("fall-alert"))
    assert status is not None
    delivery = status.deliveries[0]
    assert delivery.status == "failed"
    assert delivery.attempts == config.MAX_DELIVERY_ATTEMPTS


def test_terminal_states_are_not_reclaimed(clean_database: None) -> None:
    run(
        seed_event(
            event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    run(worker.process_once(fixed_push(PushResult(PushOutcome.DELIVERED))))
    # The delivered row is terminal: a later pass claims nothing, whatever the push.
    claimed = run(worker.process_once(fixed_push(PushResult(PushOutcome.TRANSIENT))))
    assert claimed == 0


@settings(max_examples=50)
@given(attempts=st.integers(min_value=1, max_value=100))
def test_backoff_is_bounded(attempts: int) -> None:
    # Pure function: for any attempt count, the delay stays within [0, cap].
    for _ in range(20):
        delay = worker.backoff_seconds(attempts)
        assert 0.0 <= delay <= config.BACKOFF_CAP_SECONDS
