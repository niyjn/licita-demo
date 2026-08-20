from pncp_query.services.storage import Storage


def test_storage_salva_e_lista_contrato_com_participantes(storage):

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


def test_storage_persiste_status_de_run_com_timestamps_timezone(storage):

    storage.criar_run("run-1", params_json='{"uf": "SP"}')
    storage.atualizar_run("run-1", status="running", progress=50, message="processando")
    run = storage.obter_run("run-1")

    assert run["status"] == "running"
    assert run["progress"] == 50
    assert run["message"] == "processando"
    assert "+00:00" in run["created_at"]
    assert "+00:00" in run["started_at"]


def test_storage_lista_runs_paginadas_com_filtro_e_total_de_contratos(storage):
    storage.criar_run("run-1", params_json='{"area": "TI"}')
    storage.atualizar_run("run-1", status="done")
    storage.criar_run("run-2", params_json='{"area": "SAUDE"}')
    storage.atualizar_run("run-2", status="error")
    storage.salvar_contrato(
        {
            "run_id": "run-1",
            "numero_controle": "controle-1",
            "orgao_cnpj": "12345678000195",
            "orgao_nome": "Órgão",
            "uf": "SP",
            "municipio": "São Paulo",
            "ano": "2026",
            "sequencial": "1",
            "objeto": "Software",
            "valor": "1000",
            "data_publicacao": "2026-06-01",
        },
        [],
    )

    assert [run["id"] for run in storage.listar_runs(limit=1, offset=0)] == ["run-2"]
    concluidas = storage.listar_runs(status="done")
    assert [run["id"] for run in concluidas] == ["run-1"]
    assert concluidas[0]["contratos_count"] == 1
    assert storage.contar_runs() == 2
    assert storage.contar_runs(status="error") == 1


def test_storage_permite_varias_runs_queued(storage):

    assert storage.criar_run_se_disponivel("run-1") == "run-1"
    assert storage.criar_run_se_disponivel("run-2") == "run-2"
    assert storage.contar_runs(status="queued") == 2


def test_excluir_run_remove_filhos_em_cascata(storage):
    storage.criar_run("run-1")
    contrato_id = storage.salvar_contrato(
        {
            "run_id": "run-1",
            "numero_controle": "controle-1",
            "orgao_cnpj": "12345678000195",
            "orgao_nome": "Órgão",
            "uf": "SP",
            "municipio": "São Paulo",
            "ano": "2026",
            "sequencial": "1",
            "objeto": "Software",
            "valor": "1000",
            "data_publicacao": "2026-06-01",
        },
        [{"cnpj": "11222333000181", "nome": "Empresa", "papel": "adjudicatario"}],
    )
    storage.salvar_cnpjs_auditoria(
        contrato_id,
        "run-1",
        [{"cnpj": "11222333000181", "source": "estruturada", "disposition": "vencedor"}],
    )
    storage.salvar_metricas_funil(contrato_id, "run-1", {"vencedores": 1})

    assert storage.excluir_run("run-1") is True

    with storage.connect() as cursor:
        cursor.execute("SELECT COUNT(*) AS total FROM contratos")
        assert cursor.fetchone()["total"] == 0
        cursor.execute("SELECT COUNT(*) AS total FROM participantes")
        assert cursor.fetchone()["total"] == 0
        cursor.execute("SELECT COUNT(*) AS total FROM cnpjs_auditoria")
        assert cursor.fetchone()["total"] == 0
        cursor.execute("SELECT COUNT(*) AS total FROM metricas_funil")
        assert cursor.fetchone()["total"] == 0


def test_storage_crud_perfis_busca(storage):

    perfil_id = storage.salvar_perfil("Infra", ["firewall", "data center"])
    perfis = storage.listar_perfis()

    assert perfis[0]["id"] == perfil_id
    assert perfis[0]["nome"] == "Infra"
    assert '"firewall"' in perfis[0]["termos_json"]
    assert storage.excluir_perfil(perfil_id) is True
    assert storage.listar_perfis() == []


def test_storage_persiste_evidencias_contextuais(storage):
    storage.criar_run("run-1")
    contrato_id = storage.salvar_contrato(
        {
            "run_id": "run-1",
            "numero_controle": "controle-1",
            "orgao_cnpj": "12345678000195",
            "orgao_nome": "Órgão",
            "uf": "SP",
            "municipio": "São Paulo",
            "ano": "2026",
            "sequencial": "1",
            "objeto": "Software",
            "valor": "1000",
            "data_publicacao": "2026-06-01",
        },
        [],
    )

    storage.salvar_evidencias_cnpj(
        contrato_id,
        "run-1",
        [
            {
                "cnpj": "11444777000161",
                "origin_file": "ata.pdf",
                "scan_pass": "priority",
                "page_number": 2,
                "category": "participante",
                "signal": "licitante",
                "excerpt": "Licitante 11.444.777/0001-61",
            }
        ],
    )

    evidencias = storage.listar_evidencias_cnpj("run-1", contrato_id)
    assert evidencias[0]["page_number"] == 2
    assert evidencias[0]["category"] == "participante"
