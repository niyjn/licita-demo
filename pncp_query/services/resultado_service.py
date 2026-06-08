"""Busca o resultado estruturado (adjudicatario) de uma compra no PNCP.

Usa os endpoints publicos de itens/resultados do PNCP, que retornam o
fornecedor homologado de cada item com CNPJ, razao social e valor — sem
necessidade de baixar ou interpretar PDFs.
"""

from pncp_query.services.common import somente_digitos
from pncp_query.services.http_client import HttpClient

BASE = "https://pncp.gov.br/api/pncp/v1"


class ResultadoService:
    def __init__(self, http=None):
        self.http = http or HttpClient()

    def adjudicatarios(self, orgao_cnpj, ano, sequencial):
        """Retorna a lista de adjudicatarios de uma compra.

        Cada item: {cnpj, nome, valor_homologado, numero_item}.
        Deduplica por CNPJ, somando o valor homologado.
        """
        orgao_cnpj = somente_digitos(orgao_cnpj)
        itens = self._listar_itens(orgao_cnpj, ano, sequencial)
        por_cnpj = {}
        for item in itens:
            if not item.get("temResultado"):
                continue
            numero_item = item.get("numeroItem")
            for resultado in self._resultados_item(orgao_cnpj, ano, sequencial, numero_item):
                cnpj = somente_digitos(resultado.get("niFornecedor"))
                if not cnpj:
                    continue
                registro = por_cnpj.setdefault(
                    cnpj,
                    {
                        "cnpj": cnpj,
                        "nome": resultado.get("nomeRazaoSocialFornecedor") or "",
                        "valor_homologado": 0.0,
                        "numero_item": numero_item,
                    },
                )
                registro["valor_homologado"] += float(resultado.get("valorTotalHomologado") or 0.0)
        return list(por_cnpj.values())

    def _listar_itens(self, orgao_cnpj, ano, sequencial):
        url = f"{BASE}/orgaos/{orgao_cnpj}/compras/{ano}/{sequencial}/itens"
        itens = []
        pagina = 1
        tamanho_pagina = 100
        while True:
            dados = self._get_json(url, params={"pagina": pagina, "tamanhoPagina": tamanho_pagina})
            if not dados:
                break
            
            if isinstance(dados, list):
                lista = dados
            else:
                lista = dados.get("data") or dados.get("items") or []
            
            if not lista:
                break
                
            itens.extend(lista)
            if len(lista) < tamanho_pagina:
                break
            pagina += 1
        return itens

    def _resultados_item(self, orgao_cnpj, ano, sequencial, numero_item):
        url = f"{BASE}/orgaos/{orgao_cnpj}/compras/{ano}/{sequencial}/itens/{numero_item}/resultados"
        dados = self._get_json(url)
        if isinstance(dados, list):
            return dados
        return dados.get("data") or []

    def _get_json(self, url, params=None):
        response = self.http.get(url, params=params, timeout=60)
        try:
            return response.json()
        except ValueError:
            return []
