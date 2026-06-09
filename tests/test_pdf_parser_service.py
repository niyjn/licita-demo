from pathlib import Path
from unittest.mock import patch

from pncp_query.models import ResultadoPDF
from pncp_query.services.pdf_parser_service import PDFParserService


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_extrair_resultado_flow(mock_extract):
    service = PDFParserService()

    mock_extract.return_value = (
        "Objeto: Aquisição de licenças de software e serviços de TI.\n"
        "Vencedor homologado: 11.222.333/0001-81\n"
        "CNPJ Participante: 22.333.444/0001-02",
        2,
    )

    res = service.extrair_resultado(Path("dummy.pdf"))

    assert isinstance(res, ResultadoPDF)
    assert res.page_count == 2
    assert "11222333000181" in res.cnpjs_total
    assert "22333444000102" in res.cnpjs_total
    assert {item.categoria for item in res.evidencias} == {"vencedor", "participante"}


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_extrai_cnpj_pontuado_continuo_e_separado_com_pagina(mock_extract):
    service = PDFParserService()
    mock_extract.return_value = (
        "Licitante: 11.222.333/0001-81\n"
        "Texto geral 22333444000102\n"
        "\f"
        "Empresa desclassificada CNPJ 33 444 555 0001 03",
        2,
    )

    resultado = service.extrair_resultado(Path("dummy.pdf"))

    assert resultado.cnpjs_total == ["11222333000181", "22333444000102", "33444555000103"]
    evidencias = {item.cnpj: item for item in resultado.evidencias}
    assert evidencias["11222333000181"].categoria == "participante"
    assert evidencias["22333444000102"].categoria == "incidental"
    assert evidencias["33444555000103"].categoria == "participante"
    assert evidencias["33444555000103"].pagina == 2


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_sinal_de_vencedor_prevalece_sobre_contexto_de_participacao(mock_extract):
    service = PDFParserService()
    mock_extract.return_value = (
        "Licitante vencedor e adjudicatário: 11.222.333/0001-81",
        1,
    )

    resultado = service.extrair_resultado(Path("dummy.pdf"))

    assert resultado.evidencias[0].categoria == "vencedor"
    assert resultado.evidencias[0].sinal == "vencedor"


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_sinal_de_contratante_continua_conflitante(mock_extract):
    service = PDFParserService()
    mock_extract.return_value = (
        "CNPJ da contratante: 11.222.333/0001-81",
        1,
    )

    resultado = service.extrair_resultado(Path("dummy.pdf"))

    assert resultado.evidencias[0].categoria == "conflitante"
    assert resultado.evidencias[0].sinal == "contratante"


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_sinal_de_contato_nao_virou_conflito(mock_extract):
    service = PDFParserService()
    mock_extract.return_value = (
        "Contato: 11.222.333/0001-81",
        1,
    )

    resultado = service.extrair_resultado(Path("dummy.pdf"))

    assert resultado.evidencias[0].categoria == "incidental"


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_sinal_de_responsavel_mais_proposta_permanece_participante(mock_extract):
    service = PDFParserService()
    mock_extract.return_value = (
        "Responsavel pela proposta: 11.222.333/0001-81",
        1,
    )

    resultado = service.extrair_resultado(Path("dummy.pdf"))

    assert resultado.evidencias[0].categoria == "incidental"
