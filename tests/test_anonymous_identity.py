import re
from datetime import UTC, datetime, timedelta

from app import create_app
from pncp_query.services.anonymous_identity import token_hash
from pncp_query.worker import run_once


def _csrf(client):
    response = client.get("/")
    return re.search(r'window.csrfToken = "([^"]+)"', response.get_data(as_text=True)).group(1)


def _create(client):
    response = client.post("/analises", json={"area": "TI"}, headers={"X-CSRF-Token": _csrf(client)})
    assert response.status_code == 202
    return response.json["run_id"]


def _owner(storage, client):
    _csrf(client)
    return storage.obter_identidade_por_hash(token_hash(client.get_cookie("licita_anon").value))


def test_anonymous_browser_cookie_and_run_isolation(storage):
    app = create_app({"TESTING": True, "STORAGE": storage, "ANON_COOKIE_SECURE": False})
    first, second = app.test_client(), app.test_client()
    run_id = _create(first)

    cookie = first.get_cookie("licita_anon")
    assert cookie and cookie.http_only and cookie.same_site == "Lax"
    assert first.get(f"/analises/{run_id}/status").status_code == 200
    assert second.get(f"/analises/{run_id}").status_code == 404
    assert second.get(f"/analises/{run_id}/status").status_code == 404
    assert second.get(f"/analises/{run_id}/cnpjs").status_code == 404
    assert second.get(f"/analises/{run_id}/exportar").status_code == 404
    assert second.post(f"/analises/{run_id}/excluir", headers={"X-CSRF-Token": _csrf(second)}).status_code == 404
    assert run_id.encode() not in second.get("/").data
    assert run_id.encode() not in second.get("/runs").data


def test_profiles_are_private_and_csrf_requires_exact_token(storage):
    app = create_app({"TESTING": True, "STORAGE": storage, "ANON_COOKIE_SECURE": False})
    first, second = app.test_client(), app.test_client()
    assert first.post("/perfis", json={"nome": "Infra", "termos": "firewall"}).status_code == 400
    assert (
        first.post(
            "/perfis", json={"nome": "Infra", "termos": "firewall"}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 400
    )
    created = first.post(
        "/perfis", json={"nome": "Infra", "termos": "firewall"}, headers={"X-CSRF-Token": _csrf(first)}
    )
    assert created.status_code == 201
    # The same profile name is valid for a different browser, but not this one.
    assert (
        second.post(
            "/perfis", json={"nome": "Infra", "termos": "firewall"}, headers={"X-CSRF-Token": _csrf(second)}
        ).status_code
        == 201
    )
    assert second.get("/perfis").json["perfis"][0]["id"] != created.json["id"]
    assert second.delete(f"/perfis/{created.json['id']}", headers={"X-CSRF-Token": _csrf(second)}).status_code == 404


def test_invalid_or_expired_cookie_rotates_identity(storage):
    app = create_app({"TESTING": True, "STORAGE": storage, "ANON_COOKIE_SECURE": False})
    client = app.test_client()
    original = _owner(storage, client)
    client.set_cookie("licita_anon", "not-a-valid-token")
    _csrf(client)
    rotated = _owner(storage, client)
    assert rotated["id"] != original["id"]
    storage.tocar_identidade(rotated["id"], datetime.now(UTC) - timedelta(seconds=1))
    _csrf(client)
    assert _owner(storage, client)["id"] != rotated["id"]


def test_production_cookie_is_secure(storage):
    app = create_app({"STORAGE": storage, "CSRF_SECRET": "test-secret", "ANON_COOKIE_SECURE": True})
    response = app.test_client().get("/")
    assert "; Secure" in response.headers["Set-Cookie"]


def test_csrf_active_run_and_worker_ownership(storage):
    app = create_app({"TESTING": True, "STORAGE": storage, "ANON_COOKIE_SECURE": False})
    first, second = app.test_client(), app.test_client()
    assert first.post("/analises", json={"area": "TI"}).status_code == 400
    first_run, second_run = _create(first), _create(second)
    assert first.post("/analises", json={"area": "TI"}, headers={"X-CSRF-Token": _csrf(first)}).status_code == 409

    class Executor:
        def execute(self, command, worker_name):
            storage.complete_claimed_run(command.run_id, worker_name)

    assert run_once(storage, Executor(), "worker")
    assert run_once(storage, Executor(), "worker")
    assert storage.obter_run(first_run)
    assert storage.obter_run(second_run)
    assert storage.obter_run(first_run)["status"] == "done"
    assert storage.obter_run(second_run)["status"] == "done"


def test_health_readiness_and_assets_do_not_issue_cookie(storage):
    app = create_app({"TESTING": True, "STORAGE": storage, "ANON_COOKIE_SECURE": False})
    client = app.test_client()
    for path in ("/healthz", "/readyz", "/design-system/tokens.css"):
        assert "licita_anon" not in client.get(path).headers.get("Set-Cookie", "")
