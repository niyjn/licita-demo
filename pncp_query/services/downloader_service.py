import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin

import requests

from pncp_query.config import PALAVRAS_ARQUIVO, PALAVRAS_ARQUIVO_EXCLUIR, PALAVRAS_ARQUIVO_FORTE
from pncp_query.models import ArquivoPNCP
from pncp_query.services.common import nome_seguro, somente_digitos
from pncp_query.services.http_client import HttpClient
from pncp_query.services.qualification_service import QualificationService

FILTER_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
NUMERO_CONTROLE_COMPRA_RE = re.compile(r"(?P<cnpj>\d{14})-1-(?P<sequencial>\d+)/(?P<ano>\d{4})")


class PNCPJsonError(ValueError):
    pass


@lru_cache(maxsize=512)
def _termo_filtro_pattern(termo_normalizado):
    return re.compile(rf"(^|\s){re.escape(termo_normalizado)}(\s|$)")


class DownloaderService:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"accept": "application/json", "user-agent": "pncp-query/1.0"})
        self.http = HttpClient(self.session)
        self.qualifier = QualificationService()
        self._cache_contratos = {}
        self._cache_compras = {}
        self._cache_arquivos = {}

    def listar_arquivos_relevantes(self, linha_licitacao, pdf_dir: Path, chaves_compra=None):
        chaves_compra = chaves_compra or self.resolver_chaves_compra(linha_licitacao)
        if not chaves_compra:
            return []

        orgao_cnpj, ano, numero = chaves_compra
        arquivos = self._listar_arquivos(orgao_cnpj, ano, numero)
        relevantes = []
        for indice, arquivo in enumerate(arquivos, start=1):
            titulo = self._titulo(arquivo)
            if not self._relevante(titulo):
                continue
            url = self._url_download(arquivo, orgao_cnpj, ano, numero)
            if not url:
                continue
            destino = pdf_dir / f"{orgao_cnpj}_{ano}_{numero}_{indice}_{nome_seguro(titulo)}.pdf"
            relevantes.append(ArquivoPNCP(titulo=titulo, url=url, destino=destino))
        return relevantes

    def resolver_chaves_compra(self, linha_licitacao):
        orgao_cnpj = somente_digitos(linha_licitacao.get("orgao_cnpj"))
        ano = somente_digitos(linha_licitacao.get("ano"))
        numero = str(int(somente_digitos(linha_licitacao.get("numero_sequencial")) or "0"))
        numero_controle = str(linha_licitacao.get("numero_controle_pncp") or "")
        item_url = str(linha_licitacao.get("item_url") or "")

        if not orgao_cnpj or not ano or not numero:
            return None

        if "-1-" in numero_controle or "/editais/" in item_url:
            return orgao_cnpj, ano, numero

        if "-2-" in numero_controle or "/contratos/" in item_url:
            detalhe = self._detalhar_contrato(orgao_cnpj, ano, numero)
            numero_compra = (
                detalhe.get("numeroControlePNCPCompra")
                or detalhe.get("numero_controle_pncp_compra")
                or detalhe.get("numeroControlePncpCompra")
            )
            if numero_compra:
                return self._parse_numero_controle_compra(numero_compra)
            return None

        return orgao_cnpj, ano, numero

    def qualificar_compra(self, linha_licitacao, chaves_compra):
        textos = [
            linha_licitacao.get("termo_busca", ""),
            linha_licitacao.get("title", ""),
            linha_licitacao.get("description", ""),
            linha_licitacao.get("modalidade_licitacao_nome", ""),
        ]
        detalhe = self._detalhar_compra(*chaves_compra)
        textos.extend(self._coletar_textos_objeto(detalhe))
        return self.qualifier.qualificar_ti(" ".join(str(texto) for texto in textos if texto))

    def baixar(self, arquivo: ArquivoPNCP):
        arquivo.destino.parent.mkdir(parents=True, exist_ok=True)
        if arquivo.destino.exists():
            return False
        temp_path = arquivo.destino.with_name(f".{arquivo.destino.name}.tmp")
        temp_path.unlink(missing_ok=True)
        try:
            response = self._get(arquivo.url, timeout=120)
            with temp_path.open("wb") as destino:
                destino.write(response.content)
            temp_path.replace(arquivo.destino)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return True

    def _listar_arquivos(self, orgao_cnpj, ano, numero):
        chave = (orgao_cnpj, ano, numero)
        if chave in self._cache_arquivos:
            return self._cache_arquivos[chave]
        url = f"https://pncp.gov.br/api/pncp/v1/orgaos/{orgao_cnpj}/compras/{ano}/{numero}/arquivos"
        response = self._get(url)
        dados = self._json_response(response, url)
        if isinstance(dados, list):
            arquivos = dados
        else:
            arquivos = dados.get("data") or dados.get("items") or dados.get("arquivos") or []
        self._cache_arquivos[chave] = arquivos
        return arquivos

    def _detalhar_contrato(self, orgao_cnpj, ano, numero):
        chave = (orgao_cnpj, ano, numero)
        if chave in self._cache_contratos:
            return self._cache_contratos[chave]
        urls = [
            f"https://pncp.gov.br/api/pncp/v1/orgaos/{orgao_cnpj}/contratos/{ano}/{numero}",
            f"https://pncp.gov.br/api/consulta/v1/orgaos/{orgao_cnpj}/contratos/{ano}/{numero}",
        ]
        ultimo_erro = None
        for url in urls:
            try:
                response = self._get(url)
                dados = self._json_response(response, url)
                self._cache_contratos[chave] = dados
                return dados
            except (requests.exceptions.RequestException, PNCPJsonError) as exc:
                ultimo_erro = exc
        raise ultimo_erro

    def _detalhar_compra(self, orgao_cnpj, ano, numero):
        chave = (orgao_cnpj, ano, numero)
        if chave in self._cache_compras:
            return self._cache_compras[chave]
        urls = [
            f"https://pncp.gov.br/api/pncp/v1/orgaos/{orgao_cnpj}/compras/{ano}/{numero}",
            f"https://pncp.gov.br/api/consulta/v1/orgaos/{orgao_cnpj}/compras/{ano}/{numero}",
        ]
        ultimo_erro = None
        for url in urls:
            try:
                response = self._get(url)
                dados = self._json_response(response, url)
                self._cache_compras[chave] = dados
                return dados
            except (requests.exceptions.RequestException, PNCPJsonError) as exc:
                ultimo_erro = exc
        raise ultimo_erro

    def _get(self, url, timeout=60, tentativas=5):
        return self.http.get(url, timeout=timeout, retries=tentativas)

    def _json_response(self, response, url):
        try:
            return response.json()
        except ValueError as exc:
            status = getattr(response, "status_code", "desconhecido")
            raise PNCPJsonError(f"JSON invalido em {url} status={status}: {exc}") from exc

    def _coletar_textos_objeto(self, dados):
        if isinstance(dados, dict):
            textos = []
            campos_texto = ("objeto", "descricao", "descrição", "informacao", "informação")
            for chave, valor in dados.items():
                chave_normalizada = str(chave).lower()
                if any(parte in chave_normalizada for parte in campos_texto):
                    textos.append(str(valor))
                elif isinstance(valor, dict):
                    textos.extend(self._coletar_textos_objeto(valor))
            return textos
        return []

    def _parse_numero_controle_compra(self, numero_controle):
        match = NUMERO_CONTROLE_COMPRA_RE.match(str(numero_controle))
        if not match:
            return None
        return (
            match.group("cnpj"),
            match.group("ano"),
            str(int(match.group("sequencial"))),
        )

    def _titulo(self, arquivo):
        campos = ["titulo", "title", "nome", "nomeArquivo", "descricao", "tipoDocumentoNome"]
        partes = [str(arquivo.get(campo, "")) for campo in campos if arquivo.get(campo)]
        return " ".join(partes) or "arquivo"

    def _relevante(self, titulo):
        titulo_normalizado = self._normalizar_para_filtro(titulo)
        tem_palavra_alvo = any(self._contem_termo(titulo_normalizado, palavra) for palavra in PALAVRAS_ARQUIVO)
        tem_palavra_forte = any(self._contem_termo(titulo_normalizado, palavra) for palavra in PALAVRAS_ARQUIVO_FORTE)
        tem_exclusao = any(self._contem_termo(titulo_normalizado, palavra) for palavra in PALAVRAS_ARQUIVO_EXCLUIR)

        if not tem_palavra_alvo:
            return False

        if tem_exclusao and not tem_palavra_forte:
            return False

        return True

    def _normalizar_para_filtro(self, valor):
        sem_acento = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode("ascii")
        return FILTER_NORMALIZE_RE.sub(" ", sem_acento.lower()).strip()

    def _contem_termo(self, texto_normalizado, termo):
        termo_normalizado = self._normalizar_para_filtro(termo)
        if not termo_normalizado:
            return False
        return _termo_filtro_pattern(termo_normalizado).search(texto_normalizado) is not None

    def _url_download(self, arquivo, orgao_cnpj, ano, numero):
        for campo in ("url", "uri", "link", "downloadUrl", "urlDownload"):
            if arquivo.get(campo):
                return urljoin("https://pncp.gov.br", str(arquivo[campo]))

        sequencial = arquivo.get("sequencialDocumento") or arquivo.get("sequencial") or arquivo.get("id")
        if sequencial:
            return f"https://pncp.gov.br/api/pncp/v1/orgaos/{orgao_cnpj}/compras/{ano}/{numero}/arquivos/{sequencial}"
        return ""
