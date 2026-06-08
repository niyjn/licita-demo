from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from pncp_query.services.pdf_parser_service import PDFParserService
from pncp_query.models import ResultadoPDF


def test_normalizar():
    service = PDFParserService()
    assert service._normalizar("Ação de Licitação") == "acao de licitacao"
    assert service._normalizar("CNPJ: 11.222.333/0001-81") == "cnpj 11.222.333/0001-81"


def test_detectar_primeiro_colocado():
    service = PDFParserService()
    
    # Matching first place patterns
    texto = (
        "Posicao Fornecedor CPF/CNPJ Lance Final\n"
        "1  EMPRESA VENCEDORA  11.222.333/0001-81  100,00\n"
        "2  EMPRESA PERDEDORA  22.333.444/0001-02  120,00"
    )
    res = service._detectar_primeiro_colocado(texto)
    assert "11.222.333/0001-81" in res


def test_detectar_adjudicatarios_single_cnpj():
    service = PDFParserService()
    
    # Single CNPJ with adjudicacao context indicator
    texto = "O adjudicatário homologado é o CNPJ 11.222.333/0001-81."
    cnpjs = ["11.222.333/0001-81"]
    res = service._detectar_adjudicatarios(texto, cnpjs, "ata_final.pdf")
    assert "11.222.333/0001-81" in res


def test_detectar_adjudicatarios_proximity():
    service = PDFParserService()
    
    # Proximity matching when multiple CNPJs exist
    texto = (
        "O vencedor homologado é a empresa A. "
        "CNPJ participante: 22.333.444/0001-02. "
        "Adjudicatário: 11.222.333/0001-81."
    )
    cnpjs = ["22.333.444/0001-02", "11.222.333/0001-81"]
    res = service._detectar_adjudicatarios(texto, cnpjs)
    assert "11.222.333/0001-81" in res


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_extrair_resultado_flow(mock_extract):
    service = PDFParserService()
    
    # Mock text extraction return values
    mock_extract.return_value = (
        "Objeto: Aquisição de licenças de software e serviços de TI.\n"
        "CNPJ Vencedor: 11.222.333/0001-81\n"
        "CNPJ Participante: 22.333.444/0001-02",
        2
    )
    
    caminho = Path("dummy.pdf")
    res = service.extrair_resultado(caminho)
    
    assert isinstance(res, ResultadoPDF)
    assert res.page_count == 2
    assert res.qualificado_ti is True
    assert "11.222.333/0001-81" in res.cnpjs_adjudicatarios
    assert "22.333.444/0001-02" in res.cnpjs_participantes
