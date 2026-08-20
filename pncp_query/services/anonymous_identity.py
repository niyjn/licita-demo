"""Opaque, persistent browser identities. Only token hashes are persisted."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

COOKIE_NAME = "licita_anon"


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)


def resolve_identity(storage, token, lifetime_days, refresh_hours=24):
    """Return (owner_id, raw_cookie, should_set_cookie), rotating/renewing lazily."""
    now = datetime.now(UTC)
    if token:
        identity = storage.obter_identidade_por_hash(token_hash(token))
        if identity and identity["expires_at"] > now:
            renewed = storage.renovar_identidade_se_necessario(
                identity["id"], now + timedelta(days=lifetime_days), refresh_hours
            )
            return str(identity["id"]), token, renewed
    fresh = new_token()
    owner_id = str(uuid4())
    storage.criar_identidade(owner_id, token_hash(fresh), now + timedelta(days=lifetime_days))
    return owner_id, fresh, True
