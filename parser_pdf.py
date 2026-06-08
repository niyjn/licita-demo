import argparse
from pathlib import Path

from pncp_query.config import CNPJS_BRUTOS_JSON, PDF_DIR
from pncp_query.controllers.parser_pdf_controller import ParserPDFController


def parse_args():
    parser = argparse.ArgumentParser(description="Fase 3: extrator de CNPJs em PDFs.")
    parser.add_argument("--pdf-dir", default=str(PDF_DIR), help="Pasta com PDFs a processar.")
    parser.add_argument("--saida", default=str(CNPJS_BRUTOS_JSON), help="JSON intermediario.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ParserPDFController().executar(Path(args.pdf_dir), Path(args.saida))
