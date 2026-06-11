"""
Domain models: Vocabulary of the persistence layer!
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Priority = Literal["high", "normal"]
Platform = Literal["ios", "android", "web"]
DeliveryState = Literal["pending", "delivered", "expired", "failed"]


@dataclass(frozen=True, slots=True)
class Device:
    """
    Represents a single mobile device used by hospital faculty.
    """
    user_id: str
    token: str
    platform: Platform


@dataclass(frozen=True, slots=True)
class Event:
    """
    An event detected by the digital twin. Could be anything from
    a fall, walking without aid, sudden change in respiration, &c.
    """
    event_id: str
    user_id: str
    title: str
    body: str
    priority: Priority
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ClaimedDelivery:
    """A unit of work the worker has claim. Contains everything needed to build a push."""

    event_id: str
    token: str
    user_id: str
    seq: int
    platform: Platform
    title: str
    body: str
    priority: Priority
    attempts: int


@dataclass(frozen=True, slots=True)
class IntakeResult:
    """
    Outcome of accepting an event: whether it was newly created (vs a dedup) and
    how many devices it fanned out to.
    """

    event_id: str
    created: bool
    delivery_count: int


@dataclass(frozen=True, slots=True)
class EventView:
    """An accepted event, as exposed by the status endpoint."""

    event_id: str
    user_id: str
    title: str
    body: str
    priority: Priority
    occurred_at: datetime | None
    seq: int
    received_at: datetime


@dataclass(frozen=True, slots=True)
class DeliveryView:
    """One device's delivery state for an event."""

    token: str
    status: DeliveryState
    attempts: int
    next_attempt_at: datetime
    delivered_at: datetime | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class EventStatus:
    """An event plus the state of all its deliveries - the observability view."""

    event: EventView
    deliveries: list[DeliveryView]
