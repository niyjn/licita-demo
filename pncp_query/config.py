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

PDF_DIR = Path(os.getenv("PDF_DIR", OUTPUT_DIR / "pdfs"))

# PostgreSQL is deliberately the only database backend.  Do not add a local-file
# fallback: web and worker must fail fast instead of silently using different data.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_POOL_MIN = _env_int("DB_POOL_MIN", 1)
DB_POOL_MAX = _env_int("DB_POOL_MAX", 5)

# Keep the names already used by ECS task definitions.  The short aliases make
# local configuration friendlier without changing production deployments.
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME") or os.getenv("S3_BUCKET")
AWS_REGION = os.getenv("AWS_REGION") or os.getenv("S3_REGION") or "us-east-1"


def require_database_url():
    """Return the configured PostgreSQL DSN or fail without disclosing it."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL é obrigatória; configure uma URL PostgreSQL antes de iniciar o serviço.")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL deve apontar para PostgreSQL.")
    return database_url

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
    "classificacao",
    "habilitacao",
    "inabilitacao",
    "desclassificacao",
    "proposta",
    "resultado",
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
    "classificacao",
    "habilitacao",
    "inabilitacao",
    "desclassificacao",
    "proposta",
    "resultado",
    "julgamento",
    "adjudicacao",
    "homologacao",
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
    "dfd",
    "estudo tecnico",
    "etp",
    "minuta do contrato",
    "mapa de preco",
    "mapa de precos",
    "dotacao",
    "termo de contrato",
    "termo de referencia",
    "nova data",
    "orcamento",
    "oficial",
]

PDF_MAX_BYTES = _env_int("PDF_MAX_BYTES", 15 * 1024 * 1024)
PDF_FALLBACK_MAX_FILES = _env_int("PDF_FALLBACK_MAX_FILES", 3)

def janela_padrao():
    hoje = datetime.now()
    inicio = hoje - relativedelta(months=3)
    return inicio.strftime("%Y-%m-%d"), hoje.strftime("%Y-%m-%d")
