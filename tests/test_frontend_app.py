from app import create_app


def wait_status(client, run_id, expected):
    for _ in range(50):
        response = client.get(f"/analises/{run_id}/status")
        assert response.status_code == 200
        if response.json["status"] == expected:
            return response.json
    raise AssertionError(f"status {expected} nao observado")


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


def test_post_analises_cria_run_e_status_done_no_sqlite(tmp_path):
    def fake_analysis(area, data_inicial, data_final, uf, limite, db_path, run_id=None, progress=None):
        progress({"mensagem": "metade", "atual": 1, "total": 2})

    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "analise.db", "ANALYSIS_FUNC": fake_analysis})
    client = app.test_client()

    response = client.post("/analises", json={"area": "TI", "uf": "SP", "limite": 1})

    assert response.status_code == 202
    run_id = response.json["run_id"]
    status = wait_status(client, run_id, "done")
    assert status["progress"] == 100
    assert status["error"] == ""


def test_post_analises_grava_error_quando_thread_falha(tmp_path):
    def fake_analysis(area, data_inicial, data_final, uf, limite, db_path, run_id=None, progress=None):
        raise RuntimeError("falha controlada")

    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "analise.db", "ANALYSIS_FUNC": fake_analysis})
    client = app.test_client()

    response = client.post("/analises", json={"area": "TI", "uf": "SP", "limite": 1})

    assert response.status_code == 202
    status = wait_status(client, response.json["run_id"], "error")
    assert status["progress"] == 100
    assert status["error"] == "falha controlada"


def test_status_retorna_404_para_run_inexistente(tmp_path):
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "analise.db"})

    response = app.test_client().get("/analises/run-inexistente/status")

    assert response.status_code == 404
    assert response.json == {"error": "run_not_found"}
