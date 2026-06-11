"""
Push-gateway client: POST a delivery and classify the HTTP result into a PushOutcome
(delivered / expired / rate-limited / transient / permanent / timeout) that the
worker maps to a terminal write or a retry.
"""

from dataclasses import dataclass
from enum import Enum, auto

import httpx

from models import ClaimedDelivery


class PushOutcome(Enum):
    DELIVERED = auto()  # 202 Accepted
    EXPIRED = auto()  # 410 Gone -> deactivate token
    RATE_LIMITED = auto()  # 429 -> honor Retry-After
    TRANSIENT = auto()  # 5xx / connection error -> back off and retry
    TIMEOUT = auto()  # no response in time (drop) -> back off and retry
    PERMANENT = auto()  # other 4xx -> won't succeed on retry, fail fast


@dataclass(frozen=True, slots=True)
class PushResult:
    outcome: PushOutcome
    retry_after: float | None = None
    detail: str | None = None


def _parse_retry_after(value: str | None) -> float:
    """
    Parse the Retry-After header (the mock gateway sends an integer seconds).
    """
    if value is None:
        return 1.0
    try:
        return float(value)
    except ValueError:
        return 1.0


async def push(
    client: httpx.AsyncClient,
    gateway_url: str,
    delivery: ClaimedDelivery,
    callback_url: str | None = None,
) -> PushResult:
    """
    POST a notification delivery to the push gateway and classify the result.
    """
    payload: dict[str, object] = {
        "device_token": delivery.token,
        "platform": delivery.platform,
        "event_id": delivery.event_id,
        "title": delivery.title,
        "body": delivery.body,
        "priority": delivery.priority,
    }
    if callback_url:
        # Gateway POSTs a delivery confirmation here - our crash-safety net.
        payload["callback_url"] = callback_url

    try:
        response = await client.post(f"{gateway_url}/push", json=payload)
    except httpx.TimeoutException:
        # Covers the gateway's "drop" (never responds within our timeout).
        return PushResult(PushOutcome.TIMEOUT, detail="gateway timeout")
    except httpx.HTTPError as error:
        return PushResult(PushOutcome.TRANSIENT, detail=str(error))

    match response.status_code:
        case 202:
            return PushResult(PushOutcome.DELIVERED)
        case 410:
            return PushResult(PushOutcome.EXPIRED, detail="token expired")
        case 429:
            return PushResult(
                PushOutcome.RATE_LIMITED,
                retry_after=_parse_retry_after(response.headers.get("retry-after")),
                detail="rate limited",
            )
        case status_code if 500 <= status_code < 600:
            return PushResult(PushOutcome.TRANSIENT, detail=f"http {status_code}")
        case status_code if 400 <= status_code < 500:
            # Other 4xx (410/429 matched above): a client error that won't change on
            # retry - fail fast instead of burning the attempt budget.
            return PushResult(PushOutcome.PERMANENT, detail=f"http {status_code}")
        case status_code:
            return PushResult(
                PushOutcome.TRANSIENT, detail=f"unexpected http {status_code}"
            )
