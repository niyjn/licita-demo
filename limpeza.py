import argparse
from pathlib import Path

from pncp_query.config import CNPJS_BRUTOS_JSON, CNPJS_FINAIS_CSV
from pncp_query.controllers.limpeza_controller import LimpezaController


def parse_args():
    parser = argparse.ArgumentParser(description="Fase 4: limpeza e exportacao final de CNPJs.")
    parser.add_argument("--origem", default=str(CNPJS_BRUTOS_JSON), help="JSON gerado pelo parser_pdf.py.")
    parser.add_argument("--saida", default=str(CNPJS_FINAIS_CSV), help="CSV final de CNPJs.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    LimpezaController().executar(Path(args.origem), Path(args.saida))
