from app import _titulo_limpo, create_app
from pncp_query.services.storage import Storage


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
    assert "Análise PNCP".encode() in response.data
    assert b"/design-system/tokens.css" in response.data
    assert "Funil reconciliável".encode() in response.data
    assert "Saúde".encode() in response.data
    assert "Em execução".encode() in response.data
    assert b"analysis-layout" in response.data


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


def test_index_renderiza_ultima_run_com_metricas_e_oculta_vazios(tmp_path):
    db_path = tmp_path / "analise.db"
    storage = Storage(db_path)
    storage.criar_run("run-1")
    contrato_final = storage.salvar_contrato(
        {
            "run_id": "run-1",
            "numero_controle": "final-1",
            "orgao_cnpj": "12345678000195",
            "orgao_nome": "Órgão Final",
            "uf": "SP",
            "municipio": "Sao Paulo",
            "ano": "2026",
            "sequencial": "1",
            "objeto": "CONTRATACAO DE EMPRESA ESPECIALIZADA PARA SOFTWARE",
            "valor": "1000",
            "data_publicacao": "2026-06-01",
            "status": "final",
            "motivo_status": "",
        },
        [
            {"cnpj": "11222333000181", "nome": "Empresa A", "papel": "adjudicatario"},
            {"cnpj": "11444777000161", "nome": "Empresa B", "papel": "participante"},
        ],
    )
    storage.salvar_contrato(
        {
            "run_id": "run-1",
            "numero_controle": "vazio-1",
            "orgao_cnpj": "12345678000195",
            "orgao_nome": "Órgão Vazio",
            "uf": "SP",
            "municipio": "Sao Paulo",
            "ano": "2026",
            "sequencial": "2",
            "objeto": "Objeto vazio",
            "valor": "1000",
            "data_publicacao": "2026-06-01",
            "status": "vazio",
            "motivo_status": "sem_perdedores_na_ata",
        },
        [],
    )
    storage.salvar_metricas_funil(
        contrato_final,
        "run-1",
        {
            "atas_lidas": 1,
            "cnpjs_ata_unicos": 3,
            "removido_orgao": 1,
            "removido_vencedor": 1,
            "perdedores_final": 1,
            "vencedores": 1,
            "resultado_final": 2,
        },
    )
    storage.salvar_cnpjs_auditoria(
        contrato_final,
        "run-1",
        [
            {
                "cnpj": "11444777000161",
                "source": "ata",
                "disposition": "perdedor_final",
                "reason": "cnpj_valido_da_ata",
                "origin_file": "ata-final.pdf",
            }
        ],
    )
    app = create_app({"TESTING": True, "DB_PATH": db_path})

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert "Funil reconciliável".encode() in response.data
    assert b"Atas e editais lidos" in response.data
    assert b"ata-final.pdf" in response.data
    assert b"11444777000161" in response.data
    assert b"Perdedor final" in response.data
    assert "CNPJ válido da ata".encode() in response.data
    assert b"Final" in response.data
    assert "Órgão Final".encode() in response.data
    assert "Órgão Vazio".encode() not in response.data
    assert b"Software" in response.data


def test_endpoint_cnpjs_filtra_por_disposition(tmp_path):
    db_path = tmp_path / "analise.db"
    storage = Storage(db_path)
    storage.criar_run("run-1")
    contrato_id = storage.salvar_contrato(
        {
            "run_id": "run-1",
            "numero_controle": "final-1",
            "orgao_cnpj": "12345678000195",
            "orgao_nome": "Órgão Final",
            "uf": "SP",
            "municipio": "Sao Paulo",
            "ano": "2026",
            "sequencial": "1",
            "objeto": "software",
            "valor": "1000",
            "data_publicacao": "2026-06-01",
        },
        [],
    )
    storage.salvar_cnpjs_auditoria(
        contrato_id,
        "run-1",
        [
            {"cnpj": "11444777000161", "source": "ata", "disposition": "perdedor_final"},
            {"cnpj": "11222333000181", "source": "estruturada", "disposition": "vencedor"},
        ],
    )
    app = create_app({"TESTING": True, "DB_PATH": db_path})

    response = app.test_client().get("/analises/run-1/cnpjs?disposition=perdedor_final")

    assert response.status_code == 200
    assert [item["cnpj"] for item in response.json["cnpjs"]] == ["11444777000161"]


def test_titulo_limpo_remove_prefixo_burocratico():
    titulo = _titulo_limpo(
        {
            "objeto": "CONTRATACAO DE EMPRESA ESPECIALIZADA PARA SOFTWARE DE GESTAO MUNICIPAL",
            "orgao_nome": "Órgão",
        }
    )

    assert titulo == "Software de gestão municipal"
