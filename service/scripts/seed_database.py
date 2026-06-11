"""
Seed the database directly (no running service): devices, and optionally events.

Registers --users users (1..--max-devices devices each) via the same persistence
path the API uses, then, if --events > 0, inserts that many events fanned out to
those users' devices exactly as POST /notify would. Applies the schema if the DB
is fresh, so it works against a brand-new database.

    python scripts/seed_database.py --users 50                # devices only
    python scripts/seed_database.py --users 50 --events 200   # devices + events
"""

import argparse
import asyncio
import random
import sys
import uuid
from pathlib import Path

# Make the service package importable when run as `python scripts/seed_database.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from models import Device, Event  # noqa: E402

PLATFORMS: list[str] = ["ios", "android", "web"]


async def seed_devices(users: int, max_devices: int) -> int:
    devices = [
        Device(
            user_id=f"user_{number:04d}",  # matches submit.py's user_{n:04d}
            token=f"tok_user_{number:04d}_{device_index}",
            platform=random.choice(PLATFORMS),
        )
        for number in range(1, users + 1)
        for device_index in range(random.randint(1, max_devices))
    ]
    await db.register_devices(devices)
    return len(devices)


async def seed_events(events: int, users: int, high_priority_rate: float) -> tuple[int, int]:
    results = await asyncio.gather(
        *(
            db.insert_event_and_fanout(
                Event(
                    event_id=f"evt_{uuid.uuid4().hex[:12]}",
                    user_id=f"user_{random.randint(1, users):04d}",
                    title="Seeded event",
                    body="Seeded body",
                    priority="high" if random.random() < high_priority_rate else "normal",
                )
            )
            for _ in range(events)
        )
    )
    created = sum(result.created for result in results)
    deliveries = sum(result.delivery_count for result in results)
    return created, deliveries


async def run(users: int, max_devices: int, events: int, high_priority_rate: float) -> None:
    await db.init_pool()  # opens the pool and applies the schema if absent
    try:
        device_count = await seed_devices(users, max_devices)
        print(f"registered {device_count} devices across {users} users")
        if events > 0:
            created, fanned_out = await seed_events(events, users, high_priority_rate)
            print(f"seeded {created} events, fanned out {fanned_out} deliveries")
    finally:
        await db.close_pool()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--max-devices", type=int, default=3)
    parser.add_argument("--events", type=int, default=0, help="0 = devices only")
    parser.add_argument("--high-priority-rate", type=float, default=0.05)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.users, arguments.max_devices, arguments.events, arguments.high_priority_rate))


if __name__ == "__main__":
    main()
