from pncp_query.services.storage import Storage


def test_storage_salva_e_lista_contrato_com_participantes(tmp_path):
    storage = Storage(tmp_path / "analise.db")

    storage.salvar_contrato(
        {
            "numero_controle": "11222333000181-1-1/2026",
            "orgao_cnpj": "11222333000181",
            "orgao_nome": "Orgao Teste",
            "uf": "SP",
            "municipio": "Sao Paulo",
            "ano": "2026",
            "sequencial": "1",
            "objeto": "software",
            "valor": "1000",
            "data_publicacao": "2026-06-01",
        },
        [
            {"cnpj": "11222333000181", "nome": "Empresa A", "papel": "adjudicatario", "valor_homologado": 100.0},
            {"cnpj": "11444777000161", "nome": "Empresa B", "papel": "participante"},
        ],
    )

    contratos = storage.listar_contratos("SP")

    assert len(contratos) == 1
    assert contratos[0]["numero_controle"] == "11222333000181-1-1/2026"
    assert [p["papel"] for p in contratos[0]["participantes"]] == ["adjudicatario", "participante"]


def test_storage_persiste_status_de_run_e_pragmas(tmp_path):
    storage = Storage(tmp_path / "analise.db")

    storage.criar_run("run-1", params_json='{"uf": "SP"}')
    storage.atualizar_run("run-1", status="running", progress=50, message="processando")
    run = storage.obter_run("run-1")

    assert run["status"] == "running"
    assert run["progress"] == 50
    assert run["message"] == "processando"
    with storage.connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
