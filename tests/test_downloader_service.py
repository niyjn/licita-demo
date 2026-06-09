import requests

from pncp_query.models import ArquivoPNCP
from pncp_query.services.downloader_service import (
    DocumentoInvalidoError,
    DownloaderService,
    PNCPJsonError,
)


class FakeResponse:
    def __init__(self, content=b"", json_data=None, json_error=None, status_code=200, headers=None):
        self.content = content
        self._json_data = json_data
        self._json_error = json_error
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._json_data


def test_detalhar_contrato_tenta_fallback_quando_primeiro_endpoint_nao_retorna_json():
    service = DownloaderService()
    chamadas = []

    def fake_get(url, timeout=60, tentativas=5):
        chamadas.append(url)
        if len(chamadas) == 1:
            return FakeResponse(json_error=ValueError("invalid json"), status_code=200)
        return FakeResponse(json_data={"objetoCompra": "licenca de software"}, status_code=200)

    service._get = fake_get

    assert service._detalhar_contrato("11222333000181", "2025", "1") == {"objetoCompra": "licenca de software"}
    assert len(chamadas) == 2


def test_json_invalido_gera_erro_diagnosticavel_quando_todos_falham():
    service = DownloaderService()
    service._get = lambda url, timeout=60, tentativas=5: FakeResponse(
        json_error=ValueError("invalid json"),
        status_code=502,
    )

    try:
        service._detalhar_contrato("11222333000181", "2025", "1")
    except PNCPJsonError as exc:
        assert "JSON invalido" in str(exc)
        assert "status=502" in str(exc)
    else:
        raise AssertionError("PNCPJsonError esperado")


def test_baixar_usa_arquivo_temporario_e_renomeia_no_sucesso(tmp_path):
    service = DownloaderService()
    service._get = lambda url, timeout=120: FakeResponse(
        content=b"%PDF-1.7\npdf",
        headers={"content-type": "application/pdf"},
    )
    destino = tmp_path / "arquivo.pdf"

    assert service.baixar(ArquivoPNCP("Ata", "https://example.test/file.pdf", destino))
    assert destino.read_bytes() == b"%PDF-1.7\npdf"
    assert not list(tmp_path.glob("*.tmp"))


def test_baixar_remove_temporario_quando_falha(tmp_path):
    service = DownloaderService()

    def falha(url, timeout=120):
        raise requests.exceptions.Timeout("timeout")

    service._get = falha
    destino = tmp_path / "arquivo.pdf"

    try:
        service.baixar(ArquivoPNCP("Ata", "https://example.test/file.pdf", destino))
    except requests.exceptions.Timeout:
        pass
    else:
        raise AssertionError("Timeout esperado")

    assert not destino.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_listar_arquivos_separa_prioritarios_fallback_e_ignorados(tmp_path):
    service = DownloaderService()
    service._listar_arquivos = lambda *args: [
        {"titulo": "Ata de julgamento", "url": "/ata.pdf", "sequencialDocumento": 1},
        {"titulo": "Documento complementar", "url": "/anexo.pdf", "sequencialDocumento": 2},
        {"titulo": "Termo de referência", "url": "/termo.pdf", "sequencialDocumento": 3},
        {"titulo": "Planilha de preços", "url": "/precos.xlsx", "sequencialDocumento": 4},
        {"titulo": "Ata duplicada", "url": "/ata.pdf", "sequencialDocumento": 5},
    ]

    lote = service.listar_arquivos_candidatos(
        {},
        tmp_path,
        chaves_compra=("11222333000181", "2026", "1"),
    )

    assert [arquivo.titulo for arquivo in lote.prioritarios] == ["Ata de julgamento"]
    assert [arquivo.titulo for arquivo in lote.fallback] == ["Documento complementar"]
    assert lote.ignorados == 3


def test_baixar_rejeita_conteudo_sem_assinatura_pdf(tmp_path):
    service = DownloaderService()
    service._get = lambda url, timeout=120: FakeResponse(
        content=b"<html>erro</html>",
        headers={"content-type": "text/html"},
    )
    destino = tmp_path / "arquivo.pdf"

    try:
        service.baixar(ArquivoPNCP("Ata", "https://example.test/file.pdf", destino))
    except DocumentoInvalidoError:
        pass
    else:
        raise AssertionError("DocumentoInvalidoError esperado")

    assert not destino.exists()
