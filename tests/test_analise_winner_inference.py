import analise


def test_montar_auditoria_inferi_vencedor_da_ata_quando_api_falta():
    class FakeEnrichment:
        def consultar(self, cnpj):
            return {
                "cnpj": cnpj,
                "razao_social": f"Empresa {cnpj[-4:]}",
                "nome_fantasia": f"Empresa {cnpj[-4:]}",
                "situacao_cadastral": "ATIVA",
            }

    auditoria = analise._montar_auditoria(
        adjudicatarios=[],
        cnpjs_ata={"11444777000161", "11222333000181"},
        orgao_cnpj="12345678000195",
        enrichment=FakeEnrichment(),
        evidencias=[
            {
                "cnpj": "11444777000161",
                "category": "vencedor",
                "signal": "contratada",
                "origin_file": "ata.pdf",
            },
            {
                "cnpj": "11222333000181",
                "category": "participante",
                "signal": "licitante",
                "origin_file": "ata.pdf",
            },
        ],
    )

    assert auditoria["metricas"]["vencedores_inferidos"] == 1
    assert auditoria["metricas"]["perdedores_final"] == 1
    assert auditoria["metricas"]["resultado_final"] == 2
    assert any(registro["disposition"] == "vencedor_inferido" for registro in auditoria["registros"])
    assert any(registro["disposition"] == "perdedor_final" for registro in auditoria["registros"])
