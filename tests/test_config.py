import pytest

from pncp_query.config import normalize_database_url, require_database_url


def test_normaliza_esquema_postgres_sem_tocar_em_senha_codificada(monkeypatch):
    url = "postgres://user:pa%25ss@localhost:5432/licita?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", url)

    assert require_database_url() == "postgresql://user:pa%25ss@localhost:5432/licita?sslmode=require"
    assert normalize_database_url(url) == "postgresql://user:pa%25ss@localhost:5432/licita?sslmode=require"


def test_database_url_invalida_falha_sem_expor_valor(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://secret")

    with pytest.raises(RuntimeError, match="PostgreSQL"):
        require_database_url()
