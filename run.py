import argparse

from pncp_query.config import janela_padrao
from pncp_query.controllers.rastreador_controller import RastreadorController


def parse_args():
    data_inicial, data_final = janela_padrao()
    parser = argparse.ArgumentParser(description="Fase 1: radar PNCP de contratos vigentes de TI.")
    parser.add_argument("--data-inicial", default=data_inicial, help="Data inicial YYYY-MM-DD.")
    parser.add_argument("--data-final", default=data_final, help="Data final YYYY-MM-DD.")
    parser.add_argument(
        "--limite",
        type=int,
        default=10,
        help="Limite por combinacao palavra/tipo/status. Use 0 para percorrer todas as paginas.",
    )
    parser.add_argument("--pausa", type=float, default=1.0, help="Pausa entre paginas, em segundos.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    RastreadorController().executar(args.data_inicial, args.data_final, args.limite, args.pausa)
