"""Intake contract tests (example-based).

Cover the wire behavior of /devices, /notify, and /events/{id}: status codes,
fan-out to active devices, idempotent intake, and validation.
"""

import httpx

from util import run

DEVICE: dict[str, str] = {
    "user_id": "nurse-on-call",
    "token": "nurse-phone",
    "platform": "ios",
}
EVENT: dict[str, str] = {
    "event_id": "fall-alert",
    "recipient_user_id": "nurse-on-call",
    "title": "Fall",
    "body": "Room 14",
    "priority": "high",
}


def test_health(client: httpx.AsyncClient) -> None:
    response = run(client.get("/health"))
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_register_device(client: httpx.AsyncClient) -> None:
    response = run(client.post("/devices", json=DEVICE))
    assert response.status_code == 201
    assert response.json() == {
        "user_id": "nurse-on-call",
        "token": "nurse-phone",
        "platform": "ios",
    }


def test_notify_fans_out_to_active_devices(client: httpx.AsyncClient) -> None:
    run(
        client.post(
            "/devices",
            json={
                "user_id": "nurse-on-call",
                "token": "nurse-phone",
                "platform": "ios",
            },
        )
    )
    run(
        client.post(
            "/devices",
            json={
                "user_id": "nurse-on-call",
                "token": "nurse-tablet",
                "platform": "android",
            },
        )
    )
    response = run(client.post("/notify", json=EVENT))
    assert response.status_code == 202
    status = run(client.get("/events/fall-alert")).json()
    assert len(status["deliveries"]) == 2
    assert {delivery["token"] for delivery in status["deliveries"]} == {
        "nurse-phone",
        "nurse-tablet",
    }
    assert {delivery["status"] for delivery in status["deliveries"]} == {"pending"}


def test_notify_is_idempotent_on_event_id(client: httpx.AsyncClient) -> None:
    run(client.post("/devices", json=DEVICE))
    run(client.post("/notify", json=EVENT))
    run(client.post("/notify", json=EVENT))  # upstream retry, same event_id
    status = run(client.get("/events/fall-alert")).json()
    assert len(status["deliveries"]) == 1  # not duplicated


def test_notify_with_no_devices_still_accepted(client: httpx.AsyncClient) -> None:
    event = {**EVENT, "recipient_user_id": "unregistered-user"}
    response = run(client.post("/notify", json=event))
    assert response.status_code == 202
    status = run(client.get("/events/fall-alert")).json()
    assert status["deliveries"] == []


def test_delete_device_stops_fanout(client: httpx.AsyncClient) -> None:
    run(client.post("/devices", json=DEVICE))
    response = run(client.delete("/devices/nurse-phone"))
    assert response.status_code == 200
    assert response.json()["token"] == "nurse-phone"
    run(client.post("/notify", json=EVENT))
    status = run(client.get("/events/fall-alert")).json()
    assert status["deliveries"] == []


def test_delete_unknown_token_404(client: httpx.AsyncClient) -> None:
    response = run(client.delete("/devices/does-not-exist"))
    assert response.status_code == 404


def test_get_unknown_event_404(client: httpx.AsyncClient) -> None:
    response = run(client.get("/events/does-not-exist"))
    assert response.status_code == 404


def test_invalid_platform_rejected(client: httpx.AsyncClient) -> None:
    response = run(
        client.post(
            "/devices",
            json={
                "user_id": "nurse-on-call",
                "token": "nurse-phone",
                "platform": "toaster",
            },
        )
    )
    assert response.status_code == 422
