"""
Invariants on Notification Intake.

Headline property: intake is idempotent and fans out exactly once per active
device. For ANY set of devices and ANY sequence of notify submissions (with
arbitrary repeats of event_ids), the number of deliveries for each distinct
event equals the number of active devices. Simply put, resubmitting never duplicates.
"""

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from util import make_client, run, truncate

# Short lowercase/digit identifiers keep generation fast and collision-prone
# (collisions exercise the dedup paths on purpose).
identifier = st.text(
    alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=6
)


@settings(max_examples=30, deadline=None)
@given(
    tokens=st.lists(identifier, min_size=0, max_size=4, unique=True),
    event_ids=st.lists(identifier, min_size=1, max_size=6),
)
def test_idempotent_intake_and_fanout(tokens: list[str], event_ids: list[str]) -> None:
    async def scenario() -> None:
        await truncate()
        client = make_client()
        try:
            user_id = "recipient"
            for token in tokens:
                await client.post(
                    "/devices",
                    json={"user_id": user_id, "token": token, "platform": "ios"},
                )

            for event_id in event_ids:  # list may repeat event_ids -> tests dedup
                await client.post(
                    "/notify",
                    json={
                        "event_id": event_id,
                        "recipient_user_id": user_id,
                        "title": "Alert",
                        "body": "Body",
                        "priority": "normal",
                    },
                )

            distinct_event_ids = set(event_ids)
            for event_id in distinct_event_ids:
                status = (await client.get(f"/events/{event_id}")).json()
                # Exactly one delivery per active device, regardless of resubmissions.
                assert len(status["deliveries"]) == len(tokens)
                assert {delivery["token"] for delivery in status["deliveries"]} == set(
                    tokens
                )
        finally:
            await client.aclose()

    run(scenario())
