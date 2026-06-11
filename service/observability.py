"""Structured JSON logging and lightweight in-process counters.

Every log line is one JSON object with a short event name plus arbitrary fields,
so an event's whole timeline is `grep '"event_id": "X"'` away.
"""

import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone

LOGGER_NAME = "notifier"
logger = logging.getLogger(LOGGER_NAME)
logger.setLevel(logging.INFO)

_counters: Counter[str] = Counter()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }

        if fields := getattr(record, "fields", None):
            payload.update(fields)

        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """
    Send structured JSON lines to stdout. Called on startup by the API and worker.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False


def log(message: str, **fields: object) -> None:
    """
    Emit one structured log line: a short event name plus arbitrary fields.
    """
    logger.info(message, extra={"fields": fields})


def count(name: str) -> None:
    """
    Increment an in-process counter (exposed at GET /metrics).
    """
    _counters[name] += 1


def counters() -> dict[str, int]:
    """
    Snapshot of all counters.
    """
    return dict(_counters)
