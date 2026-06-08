from pathlib import Path

import analise
from pncp_query.models import Licitacao, ResultadoPDF
from pncp_query.services.storage import Storage


class FakeSearch:
    def buscar_iter(self, *args, **kwargs):
        assert kwargs["palavras_chave"]
        assert kwargs["ufs"] == "SP"
        licitacao = Licitacao(
            termo_busca="software",
            status_busca="encerrada",
            tipo_documento_busca="edital",
            orgao_cnpj="12345678000195",
            ano="2026",
            numero_sequencial="1",
            numero_controle_pncp="12345678000195-1-000001/2026",
            orgao_nome="Orgao Teste",
            uf="SP",
            municipio_nome="Sao Paulo",
            valor_global="1000",
            data_publicacao_pncp="2026-06-01",
            title="Compra de software",
            description="Contratacao de software",
        )
        yield licitacao
        yield licitacao


class FakeDownloader:
    def resolver_chaves_compra(self, linha):
        return "12345678000195", "2026", "1"

    def listar_arquivos_relevantes(self, linha, pdf_dir, chaves):
        destino = Path(pdf_dir) / "ata.pdf"
        destino.parent.mkdir(parents=True, exist_ok=True)
        return [type("Arquivo", (), {"titulo": "Ata", "destino": destino})()]

    def baixar(self, arquivo):
        arquivo.destino.write_bytes(b"pdf")
        return True


class FakeParser:
    def extrair_resultado(self, caminho_pdf):
        return ResultadoPDF(
            arquivo=str(caminho_pdf),
            cnpjs_total=["11.222.333/0001-81", "11.444.777/0001-61", "12.345.678/0001-95"],
        )


class FakeResultado:
    def adjudicatarios(self, orgao_cnpj, ano, sequencial):
        return [{"cnpj": "11222333000181", "nome": "Empresa A", "valor_homologado": 100.0, "numero_item": 1}]


class FakeEnrichment:
    def nome(self, cnpj):
        return {"11444777000161": "Empresa B"}[cnpj]


def test_analisar_persiste_adjudicatario_e_participantes(monkeypatch, tmp_path):
    monkeypatch.setattr(analise, "PNCPSearchService", FakeSearch)
    monkeypatch.setattr(analise, "DownloaderService", FakeDownloader)
    monkeypatch.setattr(analise, "PDFParserService", FakeParser)
    monkeypatch.setattr(analise, "ResultadoService", FakeResultado)
    monkeypatch.setattr(analise, "EnrichmentService", FakeEnrichment)
    monkeypatch.setattr(analise, "PDF_DIR", tmp_path / "pdfs")
    eventos = []

    resumo = analise.analisar("TI", "2026-03-01", "2026-06-01", "SP", 10, tmp_path / "analise.db", eventos.append)

    contratos = Storage(tmp_path / "analise.db").listar_contratos("SP")
    participantes = contratos[0]["participantes"]

    assert resumo == {"contratos": 1, "participantes": 2}
    assert len(contratos) == 1
    assert [p["papel"] for p in participantes] == ["adjudicatario", "participante"]
    assert participantes[1]["nome"] == "Empresa B"
    assert any(evento["etapa"] == "concluido" for evento in eventos)
