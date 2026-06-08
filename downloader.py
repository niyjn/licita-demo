import argparse
from pathlib import Path

from pncp_query.config import LICITACOES_CSV, PDF_DIR
from pncp_query.controllers.downloader_controller import DownloaderController


def parse_args():
    parser = argparse.ArgumentParser(description="Fase 2: downloader de anexos PNCP.")
    parser.add_argument("--csv", default=str(LICITACOES_CSV), help="CSV gerado pelo run.py.")
    parser.add_argument("--pdf-dir", default=str(PDF_DIR), help="Pasta de destino dos PDFs.")
    parser.add_argument("--pausa", type=float, default=0.5, help="Pausa entre compras, em segundos.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    DownloaderController().executar(Path(args.csv), Path(args.pdf_dir), args.pausa)
