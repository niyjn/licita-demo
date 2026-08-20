import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg2
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def _database_url(base_url, database):
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def test_upgrade_adota_schema_postgresql_legado_e_preserva_dados(monkeypatch):
    admin_url = os.environ["TEST_DATABASE_URL"]
    database = f"licita_legacy_{uuid4().hex}"
    database_url = _database_url(admin_url, database)
    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{database}"')
        legacy = psycopg2.connect(database_url)
        try:
            with legacy.cursor() as cursor:
                cursor.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
                cursor.execute("INSERT INTO schema_version VALUES (8)")
                cursor.execute(
                    """CREATE TABLE runs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, progress DOUBLE PRECISION NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    params_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, worker_id TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0, heartbeat_at TEXT,
                    duration_seconds INTEGER NOT NULL DEFAULT 0
                    )"""
                )
                cursor.execute(
                    """CREATE TABLE perfis_busca (
                    id BIGSERIAL PRIMARY KEY, nome TEXT NOT NULL UNIQUE,
                    termos_json TEXT NOT NULL, created_at TEXT NOT NULL
                    )"""
                )
                cursor.execute(
                    """INSERT INTO runs (id, status, params_json, created_at, started_at, heartbeat_at)
                    VALUES ('legacy-run', 'done', '{"uf":"SP"}', '2026-01-01T10:00:00+00:00',
                    '2026-01-01T10:01:00+00:00', '2026-01-01T10:02:00+00:00')"""
                )
                cursor.execute(
                    """INSERT INTO perfis_busca (nome, termos_json, created_at)
                    VALUES ('Legado', '["firewall"]', '2026-01-01T10:00:00+00:00')"""
                )
            legacy.commit()
        finally:
            legacy.close()

        monkeypatch.setenv("DATABASE_URL", database_url)
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        command.upgrade(config, "head")

        verified = psycopg2.connect(database_url)
        try:
            with verified.cursor() as cursor:
                cursor.execute("SELECT id, created_at::text FROM runs WHERE id = 'legacy-run'")
                assert cursor.fetchone()[0] == "legacy-run"
                cursor.execute("SELECT nome FROM perfis_busca WHERE nome = 'Legado'")
                assert cursor.fetchone()[0] == "Legado"
                cursor.execute(
                    """SELECT column_name, data_type FROM information_schema.columns
                    WHERE table_name = 'runs' AND column_name IN ('created_at', 'started_at', 'heartbeat_at')"""
                )
                assert {row[1] for row in cursor.fetchall()} == {"timestamp with time zone"}
                cursor.execute("SELECT to_regclass('schema_version')")
                assert cursor.fetchone()[0] is None
                cursor.execute("SELECT version_num FROM alembic_version")
                assert cursor.fetchone()[0] == ScriptDirectory.from_config(config).get_current_head()
        finally:
            verified.close()
    finally:
        with admin.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (database,))
            cursor.execute(f'DROP DATABASE IF EXISTS "{database}"')
        admin.close()
