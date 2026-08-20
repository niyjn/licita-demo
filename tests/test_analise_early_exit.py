import analise
from pncp_query.models import Licitacao


def test_analisar_descarta_contrato_direto_sem_abrir_downloads(monkeypatch, tmp_path, storage):
    class DirectDiscardSearch:
        def buscar_iter(self, *args, **kwargs):
            yield Licitacao(
                termo_busca="software",
                status_busca="homologada",
                tipo_documento_busca="edital",
                orgao_cnpj="12345678000195",
                ano="2026",
                numero_sequencial="1",
                numero_controle_pncp="12345678000195-1-000001/2026",
                orgao_nome="Orgao Teste",
                uf="SP",
                municipio_nome="Sao Paulo",
                modalidade_licitacao_nome="Dispensa",
                situacao_nome="Homologada",
                valor_global="1000",
                data_publicacao_pncp="2026-06-01",
                title="Dispensa de licitacao",
                description="Contratacao direta",
            )

    class FailingDownloader:
        def resolver_chaves_compra(self, linha):
            raise AssertionError("resolver_chaves_compra nao deveria ser chamado")

        def listar_arquivos_candidatos(self, linha, pdf_dir, chaves):
            raise AssertionError("listar_arquivos_candidatos nao deveria ser chamado")

        def baixar(self, arquivo):
            raise AssertionError("baixar nao deveria ser chamado")

    class FailingResultado:
        def adjudicatarios(self, *args, **kwargs):
            raise AssertionError("resultado estruturado nao deveria ser chamado")

    class FailingEnrichment:
        def consultar(self, cnpj):
            raise AssertionError("enrichment nao deveria ser chamado")

    monkeypatch.setattr(analise, "PNCPSearchService", DirectDiscardSearch)
    monkeypatch.setattr(analise, "DownloaderService", FailingDownloader)
    monkeypatch.setattr(analise, "ResultadoService", FailingResultado)
    monkeypatch.setattr(analise, "PDFParserService", lambda: None)
    monkeypatch.setattr(analise, "EnrichmentService", FailingEnrichment)
    monkeypatch.setattr(analise, "PDF_DIR", tmp_path / "pdfs")
    storage.criar_run("run-1")

    resumo = analise.analisar("TI", "2026-03-01", "2026-06-01", "SP", 10, storage, run_id="run-1")

    contratos = storage.listar_contratos("SP", run_id="run-1")
    assert resumo["contratos"] == 1
    assert contratos[0]["status"] == "descartado"
    assert contratos[0]["motivo_status"] == "contratacao_direta_ou_exclusividade:dispensa"
    assert contratos[0]["participantes"] == []
    assert contratos[0]["auditoria"] == []
