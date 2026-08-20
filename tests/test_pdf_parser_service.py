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
        "Licitante: 11.222.333/0001-81\nTexto geral 22333444000102\n\fEmpresa desclassificada CNPJ 33 444 555 0001 03",
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


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_divisor_de_aguas_sem_cortar_palavras_e_sem_contaminar(mock_extract):
    service = PDFParserService()
    mock_extract.return_value = (
        "Empresa vencedora: 11.222.333/0001-81\nLicitante participante: 22.333.444/0001-02",
        1,
    )

    resultado = service.extrair_resultado(Path("dummy.pdf"))

    evidencias = {item.cnpj: item for item in resultado.evidencias}

    # Ambos devem ser classificados corretamente sem contaminação cruzada
    assert evidencias["11222333000181"].categoria == "vencedor"
    assert evidencias["22333444000102"].categoria == "participante"


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_janela_deslizante_e_limites_com_bloco_de_texto_longo(mock_extract):
    service = PDFParserService()
    # Bloco de texto substancialmente maior para testar os limites de 150 caracteres e vizinhos.
    # CNPJ 1 (11.222.333/0001-81) tem um sinal "vencedora" bem distante (mas a menos de 150 chars).
    # CNPJ 2 (22.333.444/0001-02) está próximo de CNPJ 1 (distância de ~40 caracteres).
    # CNPJ 3 (33.444.555/0001-03) está muito distante de CNPJ 2 (~300 caracteres de distância),
    #   permitindo testar a expansão completa de 150 caracteres para ambos os lados sem vizinhos próximos.
    # Colocamos a palavra "assinaturas" no meio do segundo parágrafo para que ela fique a >150 chars de distância
    # tanto do CNPJ 2 quanto do CNPJ 3, provando que ela não contaminará nenhum deles.
    texto = (
        "Declaramos como vencedora do certame o consorcio formado pela empresa lider de "
        "TI do mercado nacional de telecomunicacoes CNPJ 11.222.333/0001-81. "
        "Logo em seguida, registramos a proposta comercial do concorrente de CNPJ 22.333.444/0001-02, "
        "que ofereceu lances no lote 1.\n"
        "O processo seguiu com a analise de outros itens secundarios sem relacao com a disputa direta, "
        "ocupando espaco na ata com informacoes meramente burocraticas, assinaturas de todos os envolvidos do setor de compras e controle interno...\n"
        "Mais adiante, o pregoeiro abriu a sessao de recursos onde a empresa concorrente habilitada de "
        "CNPJ 33.444.555/0001-03 apresentou suas contrapropostas sobre o lote 2.\n"
        "Fim do documento de atas."
    )
    mock_extract.return_value = (texto, 1)

    resultado = service.extrair_resultado(Path("dummy.pdf"))
    evidencias = {item.cnpj: item for item in resultado.evidencias}

    # 1. CNPJ 1 deve capturar o sinal "vencedora" que está bem para trás no parágrafo
    assert evidencias["11222333000181"].categoria == "vencedor"
    assert "vencedora" in evidencias["11222333000181"].trecho

    # 2. CNPJ 2 e CNPJ 1 dividem o espaço do meio. CNPJ 2 deve capturar "proposta comercial"
    #    mas NÃO deve ser contaminado com o "vencedora" do CNPJ 1 (que está antes do divisor de águas)
    #    e nem com "assinaturas" que está a >150 chars para a frente.
    assert evidencias["22333444000102"].categoria == "participante"
    assert "vencedora" not in evidencias["22333444000102"].trecho
    assert "proposta comercial" in evidencias["22333444000102"].trecho

    # 3. CNPJ 3 está longe de todos. Sua janela deve expandir para trás e para frente a 150 chars.
    #    Deve capturar "habilitada" (sinal participante) a cerca de 60 chars para trás.
    #    Deve capturar "contrapropostas" (sinal participante) a 40 chars para frente.
    #    E NÃO deve capturar "assinaturas" que está a >150 chars para trás.
    assert evidencias["33444555000103"].categoria == "participante"
    assert "habilitada" in evidencias["33444555000103"].trecho
    assert "contrapropostas" in evidencias["33444555000103"].trecho
