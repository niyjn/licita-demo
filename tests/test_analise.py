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
        return {"11444777000161": "Empresa B"}.get(cnpj, "")

    def consultar(self, cnpj):
        nome = {"11444777000161": "Empresa B", "11222333000181": "Empresa A"}.get(cnpj, "")
        return {
            "cnpj": cnpj,
            "razao_social": nome,
            "nome_fantasia": nome,
            "situacao_cadastral": "ATIVA" if nome else ""
        }


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


def test_analisar_persiste_funil_auditavel_por_run(monkeypatch, tmp_path):
    monkeypatch.setattr(analise, "PNCPSearchService", FakeSearch)
    monkeypatch.setattr(analise, "DownloaderService", FakeDownloader)
    monkeypatch.setattr(analise, "PDFParserService", FakeParser)
    monkeypatch.setattr(analise, "ResultadoService", FakeResultado)
    monkeypatch.setattr(analise, "EnrichmentService", FakeEnrichment)
    monkeypatch.setattr(analise, "PDF_DIR", tmp_path / "pdfs")
    storage = Storage(tmp_path / "analise.db")
    storage.criar_run("run-1")

    resumo = analise.analisar("TI", "2026-03-01", "2026-06-01", "SP", 10, tmp_path / "analise.db", run_id="run-1")

    auditoria = storage.listar_cnpjs_auditoria("run-1")
    disposicoes = {(registro["cnpj"], registro["disposition"]) for registro in auditoria}
    metricas = storage.somar_metricas_run("run-1")

    assert ("11222333000181", "vencedor") in disposicoes
    assert ("11222333000181", "removido_vencedor") in disposicoes
    assert ("11444777000161", "perdedor_final") in disposicoes
    assert ("12345678000195", "removido_orgao") in disposicoes
    assert metricas["cnpjs_ata_unicos"] == 3
    assert metricas["removido_orgao"] == 1
    assert metricas["removido_vencedor"] == 1
    assert metricas["perdedores_final"] == 1
    assert metricas["vencedores"] == 1
    assert metricas["resultado_final"] == 2
    assert resumo["resultado_final"] == 2


def test_analisar_busca_livre_usa_termos_em_vez_da_area(monkeypatch, tmp_path):
    class CapturingSearch(FakeSearch):
        termos = None

        def buscar_iter(self, *args, **kwargs):
            type(self).termos = kwargs["palavras_chave"]
            yield from super().buscar_iter(*args, **kwargs)

    monkeypatch.setattr(analise, "PNCPSearchService", CapturingSearch)
    monkeypatch.setattr(analise, "DownloaderService", FakeDownloader)
    monkeypatch.setattr(analise, "PDFParserService", FakeParser)
    monkeypatch.setattr(analise, "ResultadoService", FakeResultado)
    monkeypatch.setattr(analise, "EnrichmentService", FakeEnrichment)
    monkeypatch.setattr(analise, "PDF_DIR", tmp_path / "pdfs")

    analise.analisar(
        None,
        "2026-03-01",
        "2026-06-01",
        "SP",
        10,
        tmp_path / "analise.db",
        termos=["firewall", "data center"],
    )

    assert CapturingSearch.termos == ["firewall", "data center"]


def test_funil_remove_no_primeiro_balde_quando_ha_sobreposicao():
    auditoria = analise._montar_auditoria(
        adjudicatarios=[],
        cnpjs_ata={"00.000.000/0000-00"},
        orgao_cnpj="00.000.000/0000-00",
        enrichment=FakeEnrichment(),
        cnpjs_origem={"00000000000000": {"ata.pdf"}},
        atas_lidas=1,
    )

    assert auditoria["metricas"]["cnpjs_ata_unicos"] == 1
    assert auditoria["metricas"]["removido_invalido"] == 1
    assert auditoria["metricas"]["removido_orgao"] == 0
    assert auditoria["registros"][0]["disposition"] == "removido_invalido"


def test_funil_remove_orgao_comprador_por_cnpj_raiz():
    auditoria = analise._montar_auditoria(
        adjudicatarios=[],
        cnpjs_ata={"12.345.678/0001-95"},  # Matriz
        orgao_cnpj="12.345.678/0002-10",  # Filial
        enrichment=FakeEnrichment(),
        cnpjs_origem={"12345678000195": {"ata.pdf"}},
        atas_lidas=1,
    )

    assert auditoria["metricas"]["cnpjs_ata_unicos"] == 1
    assert auditoria["metricas"]["removido_orgao"] == 1
    assert auditoria["registros"][0]["disposition"] == "removido_orgao"


def test_motivo_descarte_identifica_dispensa_e_inexigibilidade():
    # Test match with title
    linha_dispensa = {
        "modalidade_licitacao_nome": "Pregão Eletrônico",
        "title": "Dispensa de licitação para compra emergencial",
        "description": "Aquisição de insumos",
        "situacao_nome": "Homologada"
    }
    assert analise._motivo_descarte(linha_dispensa) == "contratacao_direta_ou_exclusividade:dispensa"

    # Test match with modalidade_licitacao_nome
    linha_inexigibilidade = {
        "modalidade_licitacao_nome": "Inexigibilidade",
        "title": "Compra de software proprietário",
        "description": "Contratação direta",
        "situacao_nome": "Homologada"
    }
    assert analise._motivo_descarte(linha_inexigibilidade) == "contratacao_direta_ou_exclusividade:inexigibilidade"

    # Test no match
    linha_valida = {
        "modalidade_licitacao_nome": "Concorrência",
        "title": "Aquisição de computadores",
        "description": "Ampla concorrência para TI",
        "situacao_nome": "Homologada"
    }
    assert analise._motivo_descarte(linha_valida) == ""
