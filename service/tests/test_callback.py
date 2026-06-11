"""Gateway delivery callback - closes the crash-window duplicate gap.

If the gateway confirms a delivery out-of-band, the row becomes terminal without
the worker, so a worker that died after the gateway committed (but before
recording) never re-sends it on recovery.
"""

import httpx

import db
from util import run, seed_event

DEVICE = {"user_id": "nurse-on-call", "token": "nurse-phone", "platform": "ios"}
EVENT = {
    "event_id": "fall-alert",
    "recipient_user_id": "nurse-on-call",
    "title": "Fall",
    "body": "Room 14",
    "priority": "high",
}


def test_callback_marks_pending_delivery_delivered(client: httpx.AsyncClient) -> None:
    run(client.post("/devices", json=DEVICE))
    run(client.post("/notify", json=EVENT))

    # Gateway confirms delivery out-of-band, before any worker recorded it.
    response = run(
        client.post(
            "/_callback",
            json={
                "event_id": "fall-alert",
                "device_token": "nurse-phone",
                "status": "delivered",
            },
        )
    )
    assert response.status_code == 200

    status = run(client.get("/events/fall-alert")).json()
    assert status["deliveries"][0]["status"] == "delivered"


def test_callback_is_idempotent(client: httpx.AsyncClient) -> None:
    run(client.post("/devices", json=DEVICE))
    run(client.post("/notify", json=EVENT))
    payload = {
        "event_id": "fall-alert",
        "device_token": "nurse-phone",
        "status": "delivered",
    }
    assert run(client.post("/_callback", json=payload)).status_code == 200
    assert (
        run(client.post("/_callback", json=payload)).status_code == 200
    )  # no error second time
    status = run(client.get("/events/fall-alert")).json()
    assert status["deliveries"][0]["status"] == "delivered"


def test_callback_delivered_row_is_not_reclaimed(clean_database: None) -> None:
    # The crux: a callback-confirmed delivery must never be claimed (re-sent) again.
    run(
        seed_event(
            event_id="fall-alert", user_id="nurse-on-call", tokens=("nurse-phone",)
        )
    )
    run(db.mark_delivered_via_callback("fall-alert", "nurse-phone"))
    claimed = run(db.claim_due(batch_size=10, lease_seconds=30))
    assert claimed == []
