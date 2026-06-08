from app import create_app


def test_index_renderiza_frontend_com_design_system(tmp_path):
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "analise.db"})

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"Analise PNCP" in response.data
    assert b"/design-system/tokens.css" in response.data
    assert b"Resultado por contrato" in response.data


def test_healthz_retorna_ok():
    app = create_app({"TESTING": True})

    response = app.test_client().get("/healthz")

    assert response.status_code == 200
    assert response.json == {"status": "ok"}


def test_post_analises_explica_placeholder_da_fundacao():
    app = create_app({"TESTING": True})

    response = app.test_client().post("/analises")

    assert response.status_code == 501
    assert response.json["error"] == "analysis_jobs_not_implemented"
