import os
from datetime import datetime
from pathlib import Path

from dateutil.relativedelta import relativedelta

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"


def _carregar_env_local():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for linha in env_path.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def _env_int(nome, padrao):
    try:
        return int(os.getenv(nome, str(padrao)))
    except ValueError:
        return padrao


def _env_float(nome, padrao):
    try:
        return float(os.getenv(nome, str(padrao)))
    except ValueError:
        return padrao


_carregar_env_local()

# Try to write to resolved paths, fallback to home directory if not writable (e.g., Docker volume permission issues)
_resolved_db_path = Path(os.getenv("DB_PATH", OUTPUT_DIR / "analise.db"))
_resolved_pdf_dir = Path(os.getenv("PDF_DIR", OUTPUT_DIR / "pdfs"))

try:
    _resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    # Test if parent directory is writable
    _test_file = _resolved_db_path.parent / ".write_test"
    _test_file.touch()
    _test_file.unlink()

    # Test if the database file itself is writable if it exists
    if _resolved_db_path.exists():
        with open(_resolved_db_path, "a"):
            pass
except Exception:
    # Fallback to user home directory (always writable by the running user)
    _resolved_db_path = Path.home() / "analise.db"
    _resolved_pdf_dir = Path.home() / "pdfs"

DB_PATH = _resolved_db_path
PDF_DIR = _resolved_pdf_dir

HTTP_MAX_RETRIES = _env_int("HTTP_MAX_RETRIES", 5)
HTTP_BACKOFF_BASE_SECONDS = _env_float("HTTP_BACKOFF_BASE_SECONDS", 2.0)
HTTP_BACKOFF_MAX_SECONDS = _env_float("HTTP_BACKOFF_MAX_SECONDS", 120.0)
HTTP_JITTER_SECONDS = _env_float("HTTP_JITTER_SECONDS", 1.0)
OCR_DPI = _env_int("OCR_DPI", 180)
OCR_MAX_PAGES = _env_int("OCR_MAX_PAGES", 20)
PDF_TEXT_MAX_PAGES = _env_int("PDF_TEXT_MAX_PAGES", 0)
PDF_TEXT_TIMEOUT_SECONDS = _env_int("PDF_TEXT_TIMEOUT_SECONDS", 180)

# Listas de exemplo, ilustrativas. Ajuste os termos conforme a area desejada.
TERMOS_TI_QUALIFICACAO = [
    "software",
    "nuvem",
    "firewall",
    "suporte tecnico",
    "data center",
    "licenciamento",
    "desenvolvimento de sistemas",
    "infraestrutura de rede",
    "banco de dados",
    "servidor",
]

AREAS = {
    "TI": TERMOS_TI_QUALIFICACAO,
    "ENGENHARIA": [
        "OBRA",
        "REFORMA",
        "PROJETO EXECUTIVO",
        "PAVIMENTACAO",
    ],
    "SAUDE": [
        "EQUIPAMENTO HOSPITALAR",
        "MEDICAMENTO",
        "INSUMO HOSPITALAR",
        "MATERIAL ODONTOLOGICO",
    ],
}

UFS = [
    "AC",
    "AL",
    "AP",
    "AM",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MT",
    "MS",
    "MG",
    "PA",
    "PB",
    "PR",
    "PE",
    "PI",
    "RJ",
    "RN",
    "RS",
    "RO",
    "RR",
    "SC",
    "SP",
    "SE",
    "TO",
]

SEARCH_PROFILES = [
    {"tipos_documento": "edital", "status": "encerrada"},
    {"tipos_documento": "edital", "status": "homologada"},
    {"tipos_documento": "contrato", "status": "vigente"},
]

PALAVRAS_ARQUIVO = [
    "ata",
    "aceite",
    "relatorio",
    "termo_aceite",
    "termo aceite",
    "termo de aceite",
    "termo de julgamento",
    "termo de adjudicacao",
    "termo de homologacao",
    "julgamento",
]

PALAVRAS_ARQUIVO_FORTE = [
    "ata",
    "aceite",
    "relatorio",
    "julgamento",
]

PALAVRAS_ARQUIVO_EXCLUIR = [
    "ato que autoriza",
    "ato de autorizacao",
    "autorizacao",
    "autorizo",
    "aviso de contratacao",
    "edital",
    "despacho",
    "declaracao de dispensa",
    "justificativa",
    "publicacao de interesse",
    "anexo de itens",
    "estudo tecnico",
    "minuta do contrato",
    "termo de contrato",
    "termo de referencia",
    "oficial",
]

def janela_padrao():
    hoje = datetime.now()
    inicio = hoje - relativedelta(months=3)
    return inicio.strftime("%Y-%m-%d"), hoje.strftime("%Y-%m-%d")
