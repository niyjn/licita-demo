"""PostgreSQL-only test infrastructure.

Set TEST_DATABASE_URL to a database the test role may use as an administrative
connection (the Docker Compose default is shown in README). Each test receives
its own database and Alembic upgrade; no test is skipped when PostgreSQL is off.
"""

import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

# Configure environment-dependent application settings before importing any
# project module. `pncp_query.config` reads these values at import time.
os.environ.setdefault("CSRF_SECRET", "pytest-only-csrf-secret")

import psycopg2
import pytest
from alembic import command
from alembic.config import Config

from pncp_query.services.storage import Storage


def _database_url(base_url, database):
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def pytest_configure():
    admin_url = os.getenv("TEST_DATABASE_URL")
    if not admin_url:
        pytest.exit("TEST_DATABASE_URL é obrigatória; inicie `docker compose up -d postgres` e configure-a.")
    os.environ.setdefault("DATABASE_URL", admin_url)


@pytest.fixture
def storage(monkeypatch):
    admin_url = os.environ["TEST_DATABASE_URL"]
    database = f"licita_test_{uuid4().hex}"
    test_url = _database_url(admin_url, database)
    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{database}"')
        monkeypatch.setenv("DATABASE_URL", test_url)
        command.upgrade(Config("alembic.ini"), "head")
        instance = Storage(test_url)
        try:
            yield instance
        finally:
            instance.close()
    finally:
        with admin.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (database,))
            cursor.execute(f'DROP DATABASE IF EXISTS "{database}"')
        admin.close()
