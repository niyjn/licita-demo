import requests

from pncp_query.services import pncp_search_service
from pncp_query.services.pncp_search_service import PNCPSearchService


class FakeResponse:
    def __init__(self, items):
        self._items = items

    def json(self):
        return {"items": self._items}


def test_busca_continua_quando_uma_combinacao_falha(monkeypatch):
    monkeypatch.setattr(
        pncp_search_service,
        "SEARCH_PROFILES",
        [
            {"tipos_documento": "edital", "status": "encerrada"},
            {"tipos_documento": "contrato", "status": "vigente"},
        ],
    )

    service = PNCPSearchService()
    logs = []
    chamadas = []
    ufs = []

    def fake_get(url, params=None, tentativas=None):
        chamadas.append(params["status"])
        ufs.append(params["ufs"])
        if params["status"] == "encerrada":
            raise requests.exceptions.ConnectionError("Remote end closed connection without response")
        return FakeResponse(
            [
                {
                    "numero_controle_pncp": "12345678000199-1-000001/2026",
                    "title": "MPLS",
                }
            ]
        )

    service._get = fake_get

    registros = list(
        service.buscar_iter(
            "2026-04-29",
            "2026-05-29",
            limite_por_combinacao=1,
            pausa=0,
            logger=logs.append,
            palavras_chave=["MPLS"],
            ufs="SP",
        )
    )

    assert chamadas == ["encerrada", "vigente"]
    assert ufs == ["SP", "SP"]
    assert len(registros) == 1
    assert registros[0].status_busca == "vigente"
    assert any("falha na consulta PNCP" in log for log in logs)
