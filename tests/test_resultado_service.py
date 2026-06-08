from pncp_query.services.resultado_service import ResultadoService


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeHttp:
    def get(self, url, params=None, timeout=60):
        if url.endswith("/itens"):
            return FakeResponse(
                {
                    "data": [
                        {"numeroItem": 1, "temResultado": True},
                        {"numeroItem": 2, "temResultado": False},
                    ]
                }
            )
        return FakeResponse(
            [
                {
                    "niFornecedor": "11.222.333/0001-81",
                    "nomeRazaoSocialFornecedor": "Empresa A",
                    "valorTotalHomologado": 50,
                },
                {
                    "niFornecedor": "11.222.333/0001-81",
                    "nomeRazaoSocialFornecedor": "Empresa A",
                    "valorTotalHomologado": 70,
                },
            ]
        )


def test_resultado_service_deduplica_adjudicatarios_por_cnpj():
    adjudicatarios = ResultadoService(http=FakeHttp()).adjudicatarios("11.222.333/0001-81", "2026", "1")

    assert adjudicatarios == [
        {
            "cnpj": "11222333000181",
            "nome": "Empresa A",
            "valor_homologado": 120.0,
            "numero_item": 1,
        }
    ]
