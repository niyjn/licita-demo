import re

WHITESPACE_RE = re.compile(r"\s+")
NON_DIGIT_RE = re.compile(r"\D")
SAFE_NAME_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


def limpar_texto(valor):
    if not isinstance(valor, str):
        return valor
    return WHITESPACE_RE.sub(" ", valor).strip()


def somente_digitos(valor):
    return NON_DIGIT_RE.sub("", str(valor or ""))


def nome_seguro(valor):
    valor = SAFE_NAME_RE.sub("_", str(valor or ""))
    return valor.strip("._")[:160] or "arquivo"
