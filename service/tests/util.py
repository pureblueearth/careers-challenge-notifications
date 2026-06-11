"""Test helpers shared by example tests and Hypothesis property tests.

We drive the async app on a single, persistent event loop (not asyncio.run per
call) so the asyncpg pool - which is bound to the loop it was created on - stays
valid across every test and every Hypothesis example.
"""

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

import httpx

import db
from api import app
from models import Device, Event, Priority

_ResultT = TypeVar("_ResultT")

# One loop for the whole test session. All coroutines run on it via run().
_loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()


def run(coroutine: Coroutine[object, object, _ResultT]) -> _ResultT:
    """
    Synchronously drive a coroutine on the shared loop.
    """
    return _loop.run_until_complete(coroutine)


async def truncate() -> None:
    """
    Reset all tables (and the seq counter) for a clean slate between tests.
    """
    async with db.pool().acquire() as connection:
        await connection.execute(
            "TRUNCATE deliveries, events, devices RESTART IDENTITY CASCADE"
        )


def make_client() -> httpx.AsyncClient:
    """
    An httpx client that calls the FastAPI app in-process (no network).
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def notify_event(
    event_id: str,
    user_id: str,
    *,
    priority: Priority = "normal",
    title: str = "Alert",
    body: str = "Body",
) -> None:
    """
    Record an event and fan out to whatever active devices the user has.
    """
    await db.insert_event_and_fanout(
        Event(
            event_id=event_id,
            user_id=user_id,
            title=title,
            body=body,
            priority=priority,
        )
    )


async def seed_event(
    event_id: str,
    user_id: str,
    tokens: tuple[str, ...],
    *,
    priority: Priority = "normal",
    title: str = "Alert",
    body: str = "Body",
) -> None:
    """
    Register devices for the user, then record + fan out an event to them.
    """
    await db.register_devices(
        [Device(user_id=user_id, token=token, platform="ios") for token in tokens]
    )
    await notify_event(event_id, user_id, priority=priority, title=title, body=body)
