import re

from app import create_app
from pncp_query.worker import run_once


def _csrf(client):
    response = client.get("/")
    return re.search(r'window.csrfToken = "([^"]+)"', response.get_data(as_text=True)).group(1)


def _create(client):
    response = client.post("/analises", json={"area": "TI"}, headers={"X-CSRF-Token": _csrf(client)})
    assert response.status_code == 202
    return response.json["run_id"]


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
    assert storage.obter_run(first_run)
    assert storage.obter_run(second_run)


def test_health_readiness_and_assets_do_not_issue_cookie(storage):
    app = create_app({"TESTING": True, "STORAGE": storage, "ANON_COOKIE_SECURE": False})
    client = app.test_client()
    for path in ("/healthz", "/readyz", "/design-system/tokens.css"):
        assert "licita_anon" not in client.get(path).headers.get("Set-Cookie", "")
