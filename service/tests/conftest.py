"""Pytest fixtures. Forces a dedicated test database before any app import."""

import os

# Must happen before importing util/db/config so the pool points at the test DB.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgres://localhost:5432/notifications_test"
)

from collections.abc import Iterator

import httpx
import pytest

import db
from util import make_client, run, truncate


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    """
    Open the pool + apply schema once for the whole session; drain at the end.
    """
    run(db.init_pool())
    yield
    run(db.close_pool())


@pytest.fixture()
def client() -> Iterator[httpx.AsyncClient]:
    """
    A fresh-state client per test: truncate, hand over a client, close it.
    """
    run(truncate())
    client_instance = make_client()
    try:
        yield client_instance
    finally:
        run(client_instance.aclose())


@pytest.fixture()
def clean_database() -> None:
    """
    Truncate before a test that drives the db/worker directly (no HTTP client).
    """
    run(truncate())
