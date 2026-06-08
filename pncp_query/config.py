import os
from datetime import datetime
from pathlib import Path

from dateutil.relativedelta import relativedelta

BASE_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = BASE_DIR / "pncp_query" / "migrations"
OUTPUT_DIR = BASE_DIR / "output"
PDF_DIR = OUTPUT_DIR / "pdfs"
EXEMPLO_DIR = BASE_DIR / "Exemplo"

LICITACOES_CSV = OUTPUT_DIR / "licitacoes_encerradas.csv"
LICITACOES_JSON = OUTPUT_DIR / "licitacoes_encerradas_bruto.json"
CNPJS_BRUTOS_JSON = OUTPUT_DIR / "cnpjs_brutos.json"
CNPJS_FINAIS_CSV = OUTPUT_DIR / "cnpjs_extraidos.csv"


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


def _env_bool(nome, padrao=False):
    valor = os.getenv(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes", "on"}


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

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://demo:demo@localhost:5432/licitacoes")
FAILURE_WEBHOOK_URL = os.getenv("FAILURE_WEBHOOK_URL", "")
FAILURE_WEBHOOK_TIMEOUT_SECONDS = _env_float("FAILURE_WEBHOOK_TIMEOUT_SECONDS", 10.0)
TARGET_WEEKLY_LEADS = _env_int("TARGET_WEEKLY_LEADS", 200)
PDF_RETENTION_DAYS = _env_int("PDF_RETENTION_DAYS", 30)
RUN_LIMIT_MIN = _env_int("RUN_LIMIT_MIN", 30)
RUN_LIMIT_DEFAULT = _env_int("RUN_LIMIT_DEFAULT", 50)
RUN_LIMIT_MAX = _env_int("RUN_LIMIT_MAX", 200)
ENABLE_DB_CHECKPOINT = _env_bool("ENABLE_DB_CHECKPOINT", True)
HTTP_MAX_RETRIES = _env_int("HTTP_MAX_RETRIES", 5)
HTTP_BACKOFF_BASE_SECONDS = _env_float("HTTP_BACKOFF_BASE_SECONDS", 2.0)
HTTP_BACKOFF_MAX_SECONDS = _env_float("HTTP_BACKOFF_MAX_SECONDS", 120.0)
HTTP_JITTER_SECONDS = _env_float("HTTP_JITTER_SECONDS", 1.0)
OCR_DPI = _env_int("OCR_DPI", 180)
OCR_MAX_PAGES = _env_int("OCR_MAX_PAGES", 20)
PDF_TEXT_MAX_PAGES = _env_int("PDF_TEXT_MAX_PAGES", 0)
PDF_TEXT_TIMEOUT_SECONDS = _env_int("PDF_TEXT_TIMEOUT_SECONDS", 180)

# NOTA: listas de exemplo, ilustrativas. Ajuste os termos conforme o nicho
# que voce quiser rastrear. Os valores abaixo sao genericos de demonstracao.
PALAVRAS_CHAVE_TI = [
    "SOFTWARE",
    "NUVEM",
    "FIREWALL",
    "SUPORTE TECNICO",
    "DATA CENTER",
    "LICENCIAMENTO",
    "DESENVOLVIMENTO DE SISTEMAS",
    "INFRAESTRUTURA DE REDE",
]

TERMOS_TI_QUALIFICACAO = [
    "software",
    "nuvem",
    "firewall",
    "data center",
    "datacenter",
    "licenciamento",
    "desenvolvimento de sistemas",
    "infraestrutura de rede",
    "suporte tecnico",
    "suporte técnico",
    "banco de dados",
    "servidor",
]

TERMOS_EXCLUSAO_SETORIAL = [
    "limpeza",
    "vigilancia",
    "vigilância",
    "merenda",
    "jardinagem",
    "portaria",
    "mao de obra",
    "mão de obra",
]

STATUS_ALVO = ["encerrada", "homologada"]
TIPOS_DOCUMENTO = ["edital"]

SEARCH_PROFILES = [
    {"tipos_documento": "edital", "status": "encerrada"},
    {"tipos_documento": "edital", "status": "homologada"},
    {"tipos_documento": "contrato", "status": "vigente"},
]

PALAVRAS_ARQUIVO = [
    "ata",
    "aceite",
    "relatorio",
    "relatório",
    "termo_aceite",
    "termo aceite",
    "termo de aceite",
    "termo de julgamento",
    "termo de adjudicacao",
    "termo de adjudicação",
    "termo de homologacao",
    "termo de homologação",
    "julgamento",
]

PALAVRAS_ARQUIVO_FORTE = [
    "ata",
    "aceite",
    "relatorio",
    "relatório",
    "julgamento",
]

PALAVRAS_ARQUIVO_EXCLUIR = [
    "ato que autoriza",
    "ato de autorizacao",
    "ato de autorização",
    "autorizacao",
    "autorização",
    "autorizo",
    "aviso de contratacao",
    "aviso de contratação",
    "edital",
    "despacho",
    "declaracao de dispensa",
    "declaração de dispensa",
    "justificativa",
    "publicacao de interesse",
    "publicação de interesse",
    "anexo de itens",
    "estudo tecnico",
    "estudo técnico",
    "minuta do contrato",
    "termo de contrato",
    "termo de referencia",
    "termo de referência",
    "oficial",
]

PADROES_VENCEDOR = [
    "proposta adjudicada",
    "adjudicacao",
    "adjudicação",
    "adjudicar",
    "adjudicou",
    "adjudicado e homologado",
    "adjudicada e homologada",
    "homologacao",
    "homologação",
    "homologado",
    "vencedor",
    "empresa vencedora",
    "fornecedor vencedor",
]


def janela_padrao():
    hoje = datetime.now()
    inicio = hoje - relativedelta(months=1)
    return inicio.strftime("%Y-%m-%d"), hoje.strftime("%Y-%m-%d")
