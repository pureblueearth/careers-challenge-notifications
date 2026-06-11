"""
Intake HTTP API (FastAPI): device registration, idempotent /notify with
fan-out, and an event-status endpoints. Delivery is handled by the worker.

Run:  uvicorn api:app --host 0.0.0.0 --port 8080
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import db
import observability
from models import Device, Event, EventStatus, IntakeResult, Platform, Priority


class DeviceRegistration(BaseModel):
    """
    Data Transfer Object for POST /devices.
    """

    user_id: str
    token: str
    platform: Platform

    def to_device(self) -> Device:
        return Device(user_id=self.user_id, token=self.token, platform=self.platform)


class NotifyRequest(BaseModel):
    """
    Data Transfer Object for POST /notify.
    """

    event_id: str
    recipient_user_id: str
    title: str
    body: str
    priority: Priority = "normal"
    occurred_at: datetime | None = None

    def to_event(self) -> Event:
        return Event(
            event_id=self.event_id,
            user_id=self.recipient_user_id,
            title=self.title,
            body=self.body,
            priority=self.priority,
            occurred_at=self.occurred_at,
        )


class DeliveryCallback(BaseModel):
    """
    Gateway's per-device delivery confirmation (POSTed to /_callback).
    """

    event_id: str
    device_token: str
    status: str | None = None
    delivered_at: datetime | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Startup: open the pool and apply the schema before serving any request.
    observability.configure_logging()
    await db.init_pool()
    yield
    # Shutdown: drain the pool.
    await db.close_pool()


app = FastAPI(title="notification-service", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    """
    Liveness + DB readiness. 503 (not 200) if the DB round-trip fails, so we
    distinguish 'process up but DB down' from healthy.
    """
    try:
        await db.ping()
        return JSONResponse(content={"ok": True})
    except Exception as error:  # noqa: BLE001 - surface any DB error as not-ready
        return JSONResponse(status_code=503, content={"ok": False, "error": str(error)})


@app.get("/metrics")
async def metrics() -> dict[str, int]:
    return {**observability.counters(), **await db.delivery_status_counts()}


@app.post("/devices", status_code=201)
async def register_device(registration: DeviceRegistration) -> Device:
    return await db.register_device(registration.to_device())


@app.delete("/devices/{token}")
async def delete_device(token: str) -> Device:
    if not (device := await db.deactivate_device(token)):
        raise HTTPException(status_code=404, detail="unknown token")

    return device


@app.post("/notify", status_code=202)
async def notify(request: NotifyRequest) -> IntakeResult:
    """
    Validate, idempotently record the event, fan out pending deliveries, ack
    fast. Actual delivery is asynchronous (the worker).
    """
    result = await db.insert_event_and_fanout(request.to_event())
    observability.log(
        "notify_accepted",
        event_id=request.event_id,
        user_id=request.recipient_user_id,
        priority=request.priority,
    )
    observability.count("notify_accepted")
    return result


@app.post("/_callback")
async def delivery_callback(callback: DeliveryCallback) -> dict[str, bool]:
    """
    Gateway delivery confirmation: mark the (event, device) delivered out-of-band.
    """
    await db.mark_delivered_via_callback(callback.event_id, callback.device_token)
    observability.count("callback")
    return {"ok": True}


@app.get("/events/{event_id}")
async def event_status(event_id: str) -> EventStatus:
    """
    The event and the status of all its deliveries.
    """
    status = await db.get_event_status(event_id)
    if status is None:
        raise HTTPException(status_code=404, detail="unknown event_id")
    return status
