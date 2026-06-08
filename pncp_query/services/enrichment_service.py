"""Enriquece um CNPJ com a razao social via BrasilAPI (publica, sem chave).

Usada para obter o nome das empresas participantes (perdedoras) que aparecem
apenas como CNPJ no texto das atas. Fail-safe: se a API falhar ou houver
rate limit, retorna nome vazio em vez de quebrar o pipeline.
"""

from pncp_query.services.common import somente_digitos
from pncp_query.services.http_client import HttpClient

BASE = "https://brasilapi.com.br/api/cnpj/v1"


class EnrichmentService:
    def __init__(self, http=None):
        self.http = http or HttpClient()
        self._cache = {}

    def nome(self, cnpj):
        dados = self.consultar(cnpj)
        return dados.get("razao_social", "") if dados else ""

    def consultar(self, cnpj):
        cnpj = somente_digitos(cnpj)
        if not cnpj or len(cnpj) != 14:
            return {}
        if cnpj in self._cache:
            return self._cache[cnpj]
        dados = self._buscar(cnpj)
        self._cache[cnpj] = dados
        return dados

    def _buscar(self, cnpj):
        try:
            response = self.http.get(f"{BASE}/{cnpj}", timeout=30, retries=2)
            payload = response.json()
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            "cnpj": cnpj,
            "razao_social": payload.get("razao_social", "") or "",
            "nome_fantasia": payload.get("nome_fantasia", "") or "",
            "uf": payload.get("uf", "") or "",
            "municipio": payload.get("municipio", "") or "",
            "porte": payload.get("porte", "") or "",
        }
