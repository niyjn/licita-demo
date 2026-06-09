from pathlib import Path

import analise
from pncp_query.models import ArquivoPNCP, EvidenciaCNPJ, Licitacao, LoteArquivosPNCP, ResultadoPDF
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

    def listar_arquivos_candidatos(self, linha, pdf_dir, chaves):
        destino = Path(pdf_dir) / "ata.pdf"
        destino.parent.mkdir(parents=True, exist_ok=True)
        return LoteArquivosPNCP(
            prioritarios=[ArquivoPNCP("Ata", "https://example.test/ata.pdf", destino)]
        )

    def baixar(self, arquivo):
        arquivo.destino.write_bytes(b"pdf")
        return True


class FakeParser:
    def extrair_resultado(self, caminho_pdf):
        return ResultadoPDF(
            arquivo=str(caminho_pdf),
            cnpjs_total=["11.222.333/0001-81", "11.444.777/0001-61", "12.345.678/0001-95"],
            evidencias=[
                EvidenciaCNPJ("11222333000181", 1, "Vencedor 11.222.333/0001-81", "conflitante", "vencedor"),
                EvidenciaCNPJ("11444777000161", 1, "Licitante 11.444.777/0001-61", "participante", "licitante"),
                EvidenciaCNPJ("12345678000195", 1, "Órgão 12.345.678/0001-95", "conflitante", "orgao comprador"),
            ],
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


def test_funil_mantem_cnpj_incidental_como_inconclusivo():
    auditoria = analise._montar_auditoria(
        adjudicatarios=[{"cnpj": "11222333000181", "nome": "Vencedor"}],
        cnpjs_ata={"11444777000161"},
        orgao_cnpj="12345678000195",
        enrichment=FakeEnrichment(),
        evidencias=[
            {
                "cnpj": "11444777000161",
                "category": "incidental",
                "signal": "",
                "origin_file": "anexo.pdf",
            }
        ],
    )

    assert auditoria["metricas"]["perdedores_final"] == 0
    assert auditoria["metricas"]["candidatos_inconclusivos"] == 1
    assert auditoria["registros"][-1]["reason"] == "sem_contexto_explicito"


def test_funil_nao_confirma_perdedor_sem_vencedor_estruturado():
    auditoria = analise._montar_auditoria(
        adjudicatarios=[],
        cnpjs_ata={"11444777000161"},
        orgao_cnpj="12345678000195",
        enrichment=FakeEnrichment(),
        evidencias=[
            {
                "cnpj": "11444777000161",
                "category": "participante",
                "signal": "licitante",
                "origin_file": "ata.pdf",
            }
        ],
    )

    assert auditoria["metricas"]["perdedores_final"] == 0
    assert auditoria["registros"][-1]["reason"] == "vencedores_indisponiveis"


def test_fallback_so_e_processado_quando_prioritario_nao_confirma_perdedor(monkeypatch, tmp_path):
    class TwoPassDownloader(FakeDownloader):
        def baixar(self, arquivo):
            arquivo.destino.write_bytes(arquivo.destino.name.encode())
            return True

        def listar_arquivos_candidatos(self, linha, pdf_dir, chaves):
            pdf_dir = Path(pdf_dir)
            pdf_dir.mkdir(parents=True, exist_ok=True)
            return LoteArquivosPNCP(
                prioritarios=[
                    ArquivoPNCP("Ata", "https://example.test/ata.pdf", pdf_dir / "priority.pdf")
                ],
                fallback=[
                    ArquivoPNCP(
                        "Documento complementar",
                        "https://example.test/anexo.pdf",
                        pdf_dir / "fallback.pdf",
                        prioridade="fallback",
                    )
                ],
            )

    class TwoPassParser:
        arquivos = []

        def extrair_resultado(self, caminho_pdf):
            self.arquivos.append(caminho_pdf.name)
            categoria = "incidental" if caminho_pdf.name == "priority.pdf" else "participante"
            return ResultadoPDF(
                arquivo=str(caminho_pdf),
                cnpjs_total=["11444777000161"],
                evidencias=[
                    EvidenciaCNPJ(
                        "11444777000161",
                        1,
                        "Licitante 11.444.777/0001-61" if categoria == "participante" else "CNPJ cadastrado",
                        categoria,
                        "licitante" if categoria == "participante" else "",
                    )
                ],
            )

    monkeypatch.setattr(analise, "PNCPSearchService", FakeSearch)
    monkeypatch.setattr(analise, "DownloaderService", TwoPassDownloader)
    monkeypatch.setattr(analise, "PDFParserService", TwoPassParser)
    monkeypatch.setattr(analise, "ResultadoService", FakeResultado)
    monkeypatch.setattr(analise, "EnrichmentService", FakeEnrichment)
    monkeypatch.setattr(analise, "PDF_DIR", tmp_path / "pdfs")
    storage = Storage(tmp_path / "analise.db")
    storage.criar_run("run-1")

    resumo = analise.analisar(
        "TI",
        "2026-03-01",
        "2026-06-01",
        "SP",
        10,
        tmp_path / "analise.db",
        run_id="run-1",
    )

    assert TwoPassParser.arquivos == ["priority.pdf", "fallback.pdf"]
    assert resumo["documentos_prioritarios_lidos"] == 1
    assert resumo["documentos_fallback_lidos"] == 1
    assert resumo["perdedores_final"] == 1


def test_fallback_nao_e_processado_quando_prioritario_confirma_perdedor(monkeypatch, tmp_path):
    class ConfirmingParser:
        arquivos = []

        def extrair_resultado(self, caminho_pdf):
            self.arquivos.append(caminho_pdf.name)
            return ResultadoPDF(
                arquivo=str(caminho_pdf),
                cnpjs_total=["11444777000161"],
                evidencias=[
                    EvidenciaCNPJ(
                        "11444777000161",
                        1,
                        "Licitante 11.444.777/0001-61",
                        "participante",
                        "licitante",
                    )
                ],
            )

    class TwoPassDownloader(FakeDownloader):
        def listar_arquivos_candidatos(self, linha, pdf_dir, chaves):
            pdf_dir = Path(pdf_dir)
            pdf_dir.mkdir(parents=True, exist_ok=True)
            return LoteArquivosPNCP(
                prioritarios=[
                    ArquivoPNCP("Ata", "https://example.test/ata.pdf", pdf_dir / "priority.pdf")
                ],
                fallback=[
                    ArquivoPNCP(
                        "Anexo",
                        "https://example.test/anexo.pdf",
                        pdf_dir / "fallback.pdf",
                        prioridade="fallback",
                    )
                ],
            )

    monkeypatch.setattr(analise, "PNCPSearchService", FakeSearch)
    monkeypatch.setattr(analise, "DownloaderService", TwoPassDownloader)
    monkeypatch.setattr(analise, "PDFParserService", ConfirmingParser)
    monkeypatch.setattr(analise, "ResultadoService", FakeResultado)
    monkeypatch.setattr(analise, "EnrichmentService", FakeEnrichment)
    monkeypatch.setattr(analise, "PDF_DIR", tmp_path / "pdfs")

    analise.analisar("TI", "2026-03-01", "2026-06-01", "SP", 10, tmp_path / "analise.db")

    assert ConfirmingParser.arquivos == ["priority.pdf"]


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
