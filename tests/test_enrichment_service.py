from pncp_query.services.enrichment_service import EnrichmentService


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self, payload=None, exc=None):
        self.payload = payload or {}
        self.exc = exc

    def get(self, url, timeout=30, retries=2):
        if self.exc:
            raise self.exc
        return FakeResponse(self.payload)


def test_enrichment_service_retorna_nome():
    service = EnrichmentService(http=FakeHttp({"razao_social": "Empresa Participante"}))

    dados = service.consultar("11.222.333/0001-81")
    assert dados.get("razao_social") == "Empresa Participante"


def test_enrichment_service_falha_sem_quebrar():
    service = EnrichmentService(http=FakeHttp(exc=TimeoutError("timeout")))

    assert service.consultar("11.222.333/0001-81") == {}
