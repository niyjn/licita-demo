from pathlib import Path
from unittest.mock import patch

from pncp_query.models import ResultadoPDF
from pncp_query.services.pdf_parser_service import PDFParserService


@patch.object(PDFParserService, "_extrair_texto_nativo")
def test_extrair_resultado_flow(mock_extract):
    service = PDFParserService()

    # Mock text extraction returning two CNPJs and the page count.
    mock_extract.return_value = (
        "Objeto: Aquisição de licenças de software e serviços de TI.\n"
        "Vencedor homologado: 11.222.333/0001-81\n"
        "CNPJ Participante: 22.333.444/0001-02",
        2,
    )

    res = service.extrair_resultado(Path("dummy.pdf"))

    assert isinstance(res, ResultadoPDF)
    assert res.page_count == 2
    assert "11.222.333/0001-81" in res.cnpjs_total
    assert "22.333.444/0001-02" in res.cnpjs_total
