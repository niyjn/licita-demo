from pncp_query.services.storage import Storage


def test_storage_persiste_metricas_de_vencedor_inferido(tmp_path):
    storage = Storage(tmp_path / "analise.db")
    storage.criar_run("run-1")
    contrato_id = storage.salvar_contrato(
        {
            "run_id": "run-1",
            "numero_controle": "controle-1",
            "orgao_cnpj": "12345678000195",
            "orgao_nome": "Orgao",
            "uf": "SP",
            "municipio": "Sao Paulo",
            "ano": "2026",
            "sequencial": "1",
            "objeto": "Software",
            "valor": "1000",
            "data_publicacao": "2026-06-01",
        },
        [],
    )

    storage.salvar_metricas_funil(
        contrato_id,
        "run-1",
        {"vencedores": 0, "vencedores_inferidos": 1, "resultado_final": 1},
    )

    metricas = storage.somar_metricas_run("run-1")
    assert metricas["vencedores_inferidos"] == 1
    assert metricas["resultado_final"] == 1
