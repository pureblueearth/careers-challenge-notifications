"""Structured logging and counters."""

import logging

import db
import observability
import worker
from gateway import PushOutcome, PushResult
from models import ClaimedDelivery
from util import run, seed_event


class CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def _delivered(delivery: ClaimedDelivery) -> PushResult:
    return PushResult(PushOutcome.DELIVERED)


def test_delivery_emits_structured_log(clean_database: None) -> None:
    capture = CapturingHandler()
    observability.logger.addHandler(capture)
    try:
        run(
            seed_event(
                event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
            )
        )
        run(worker.process_once(_delivered))
    finally:
        observability.logger.removeHandler(capture)

    delivery_logs = [
        record for record in capture.records if record.getMessage() == "delivery"
    ]
    assert delivery_logs, "expected a 'delivery' log line"
    fields = getattr(delivery_logs[0], "fields", {})
    assert fields.get("event_id") == "fall-alert"
    assert fields.get("token") == "nurse-phone"
    assert fields.get("outcome") == "delivered"


def test_counters_increment_on_delivery(clean_database: None) -> None:
    before = observability.counters().get("delivered", 0)
    run(
        seed_event(
            event_id="second-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    run(worker.process_once(_delivered))
    assert observability.counters().get("delivered", 0) == before + 1
