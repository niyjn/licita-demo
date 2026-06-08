import re
import unicodedata
from functools import lru_cache

from pncp_query.config import TERMOS_EXCLUSAO_SETORIAL, TERMOS_TI_QUALIFICACAO

NORMALIZE_RE = re.compile(r"[^a-z0-9.+/#-]+")


@lru_cache(maxsize=512)
def _termo_pattern(termo_normalizado):
    return re.compile(rf"(^|\s){re.escape(termo_normalizado)}(\s|$)")


class QualificationService:
    def qualificar_ti(self, texto):
        texto_normalizado = self.normalizar(texto)
        inclusoes = [termo for termo in TERMOS_TI_QUALIFICACAO if self.contem_termo(texto_normalizado, termo)]
        exclusoes = [termo for termo in TERMOS_EXCLUSAO_SETORIAL if self.contem_termo(texto_normalizado, termo)]
        return {
            "qualificado": bool(inclusoes) and not exclusoes,
            "inclusoes": inclusoes,
            "exclusoes": exclusoes,
        }

    def normalizar(self, valor):
        sem_acento = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode("ascii")
        return NORMALIZE_RE.sub(" ", sem_acento.lower()).strip()

    def contem_termo(self, texto_normalizado, termo):
        termo_normalizado = self.normalizar(termo)
        if not termo_normalizado:
            return False
        return _termo_pattern(termo_normalizado).search(texto_normalizado) is not None
