import re
import time

import requests

from pncp_query.config import AREAS, SEARCH_PROFILES
from pncp_query.models import Licitacao
from pncp_query.services.common import limpar_texto
from pncp_query.services.http_client import HttpClient

NUMERO_CONTROLE_RE = re.compile(r"(?P<cnpj>\d{14})-\d-(?P<seq>\d+)/(?P<ano>\d{4})")


class PNCPSearchService:
    def __init__(self):
        self.base_url = "https://pncp.gov.br/api/search/"
        self.session = requests.Session()
        self.session.headers.update({"accept": "application/json", "user-agent": "pncp-query/1.0"})
        self.http = HttpClient(self.session)

    def buscar_iter(
        self,
        data_inicial,
        data_final,
        limite_por_combinacao=0,
        pausa=1.0,
        logger=print,
        palavras_chave=None,
        ufs=None,
    ):
        termos = palavras_chave or AREAS["TI"]
        for palavra in termos:
            for profile in SEARCH_PROFILES:
                yield from self._buscar_combinacao(
                    palavra,
                    profile["tipos_documento"],
                    profile["status"],
                    data_inicial,
                    data_final,
                    limite_por_combinacao,
                    pausa,
                    logger,
                    ufs,
                )

    def _buscar_combinacao(
        self,
        palavra,
        tipo_documento,
        status,
        data_inicial,
        data_final,
        limite_por_combinacao,
        pausa,
        logger,
        ufs=None,
    ):
        pagina = 1
        coletados = 0
        registros = []
        logger(f"Buscando '{palavra}' | tipo={tipo_documento} | status={status}")

        while True:
            params = {
                "q": palavra,
                "dataPublicacaoPncpInicial": data_inicial,
                "dataPublicacaoPncpFinal": data_final,
                "pagina": pagina,
                "tamanhoPagina": 50,
                "tipos_documento": tipo_documento,
                "status": status,
            }
            if ufs:
                params["ufs"] = ufs
            try:
                response = self._get(self.base_url, params=params)
            except requests.exceptions.RequestException as exc:
                logger(
                    "  -> falha na consulta PNCP; combinacao ignorada: "
                    f"pagina={pagina}; coletados={coletados}; erro={exc}"
                )
                break
            items = response.json().get("items", [])
            if not items:
                logger(f"  -> fim dos resultados. Coletados nesta combinacao: {coletados}")
                break

            for item in items:
                registros.append(self._normalizar(item, palavra, status, tipo_documento))
                coletados += 1
                if limite_por_combinacao and coletados >= limite_por_combinacao:
                    break

            logger(f"  -> pagina {pagina}: {len(items)} itens; coletados={coletados}")
            if limite_por_combinacao and coletados >= limite_por_combinacao:
                break

            pagina += 1
            time.sleep(pausa)
        return registros

    def _get(self, url, params=None, tentativas=None):
        kwargs = {"params": params, "timeout": 60}
        if tentativas is not None:
            kwargs["retries"] = tentativas
        return self.http.get(url, **kwargs)

    def _normalizar(self, item, termo_busca, status_busca, tipo_documento):
        item_url = item.get("item_url") or ""
        partes = [parte for parte in item_url.split("/") if parte]
        orgao_cnpj = str(item.get("orgao_cnpj") or "")
        ano = str(item.get("ano") or "")
        numero_sequencial = str(item.get("numero_sequencial") or "")

        if len(partes) >= 4:
            orgao_cnpj = orgao_cnpj or partes[-3]
            ano = ano or partes[-2]
            numero_sequencial = numero_sequencial or partes[-1]

        numero_controle = item.get("numero_controle_pncp") or item.get("numeroControlePNCP") or ""
        match = NUMERO_CONTROLE_RE.match(str(numero_controle))
        if match:
            orgao_cnpj = orgao_cnpj or match.group("cnpj")
            ano = ano or match.group("ano")
            numero_sequencial = numero_sequencial or str(int(match.group("seq")))

        return Licitacao(
            termo_busca=termo_busca,
            status_busca=status_busca,
            tipo_documento_busca=tipo_documento,
            orgao_cnpj=orgao_cnpj,
            ano=ano,
            numero_sequencial=numero_sequencial,
            numero_controle_pncp=str(numero_controle),
            orgao_nome=limpar_texto(item.get("orgao_nome", "")),
            uf=str(item.get("uf", "")),
            municipio_nome=limpar_texto(item.get("municipio_nome", "")),
            modalidade_licitacao_nome=limpar_texto(item.get("modalidade_licitacao_nome", "")),
            situacao_nome=limpar_texto(item.get("situacao_nome", "")),
            valor_global=str(item.get("valor_global", "")),
            data_publicacao_pncp=str(item.get("data_publicacao_pncp", "")),
            data_atualizacao_pncp=str(item.get("data_atualizacao_pncp", "")),
            title=limpar_texto(item.get("title", "")),
            description=limpar_texto(item.get("description", "")),
            item_url=str(item.get("item_url", "")),
        )
