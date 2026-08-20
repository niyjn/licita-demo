import re
import unicodedata
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin

import requests

from pncp_query.config import (
    PALAVRAS_ARQUIVO,
    PALAVRAS_ARQUIVO_EXCLUIR,
    PALAVRAS_ARQUIVO_FORTE,
    PDF_MAX_BYTES,
    S3_BUCKET_NAME,
    AWS_REGION,
)
from pncp_query.models import ArquivoPNCP, LoteArquivosPNCP
from pncp_query.services.common import nome_seguro, somente_digitos
from pncp_query.services.http_client import HttpClient

FILTER_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
NUMERO_CONTROLE_COMPRA_RE = re.compile(r"(?P<cnpj>\d{14})-1-(?P<sequencial>\d+)/(?P<ano>\d{4})")


class PNCPJsonError(ValueError):
    pass


class DocumentoInvalidoError(ValueError):
    pass


@lru_cache(maxsize=512)
def _termo_filtro_pattern(termo_normalizado):
    return re.compile(rf"(^|\s){re.escape(termo_normalizado)}(\s|$)")


class DownloaderService:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"accept": "application/json", "user-agent": "pncp-query/1.0"})
        self.http = HttpClient(self.session)
        self._cache_contratos = {}
        self._cache_arquivos = {}
        self.s3_client = None
        if S3_BUCKET_NAME:
            import boto3
            self.s3_client = boto3.client("s3", region_name=AWS_REGION)

    def listar_arquivos_relevantes(self, linha_licitacao, pdf_dir: Path, chaves_compra=None):
        return self.listar_arquivos_candidatos(
            linha_licitacao,
            pdf_dir,
            chaves_compra,
        ).prioritarios

    def listar_arquivos_candidatos(self, linha_licitacao, pdf_dir: Path, chaves_compra=None):
        chaves_compra = chaves_compra or self.resolver_chaves_compra(linha_licitacao)
        if not chaves_compra:
            return LoteArquivosPNCP()

        orgao_cnpj, ano, numero = chaves_compra
        arquivos = self._listar_arquivos(orgao_cnpj, ano, numero)
        lote = LoteArquivosPNCP()
        urls_vistas = set()
        for indice, arquivo in enumerate(arquivos, start=1):
            titulo = self._titulo(arquivo)
            url = self._url_download(arquivo, orgao_cnpj, ano, numero)
            if not url or url in urls_vistas:
                lote.ignorados += 1
                continue
            urls_vistas.add(url)
            categoria = self._classificar_documento(titulo, url, arquivo)
            if categoria == "ignored":
                lote.ignorados += 1
                continue
            destino = pdf_dir / f"{orgao_cnpj}_{ano}_{numero}_{indice}_{nome_seguro(titulo)}.pdf"
            sequencial = arquivo.get("sequencialDocumento") or arquivo.get("sequencial") or arquivo.get("id") or ""
            candidato = ArquivoPNCP(
                titulo=titulo,
                url=url,
                destino=destino,
                prioridade=categoria,
                sequencial=str(sequencial),
            )
            if categoria == "priority":
                lote.prioritarios.append(candidato)
            else:
                lote.fallback.append(candidato)
        return lote

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

    def baixar(self, arquivo: ArquivoPNCP, *, run_id=None, compra=None):
        """Download a PDF locally and, when enabled, upload it under a non-colliding S3 key.

        The return value is document metadata for persistence by the analysis
        transaction; ``None`` keeps compatibility with already-present files.
        """
        arquivo.destino.parent.mkdir(parents=True, exist_ok=True)
        if arquivo.destino.exists():
            return None
        temp_path = arquivo.destino.with_name(f".{arquivo.destino.name}.tmp")
        temp_path.unlink(missing_ok=True)
        try:
            response = self._get(arquivo.url, timeout=120)
            conteudo = response.content
            if len(conteudo) > PDF_MAX_BYTES:
                raise DocumentoInvalidoError(
                    f"Documento excede o limite de {PDF_MAX_BYTES} bytes: {arquivo.titulo}"
                )
            content_type = str(getattr(response, "headers", {}).get("content-type", "")).lower()
            if content_type and "pdf" not in content_type and "octet-stream" not in content_type:
                raise DocumentoInvalidoError(
                    f"Documento não é PDF (Content-Type {content_type}): {arquivo.titulo}"
                )
            if not conteudo.lstrip().startswith(b"%PDF"):
                raise DocumentoInvalidoError(f"Documento sem assinatura PDF: {arquivo.titulo}")
            
            digest = sha256(conteudo).hexdigest()
            s3_key = None
            if self.s3_client:
                s3_key = self._s3_key(arquivo, run_id, compra)
                self.s3_client.put_object(
                    Bucket=S3_BUCKET_NAME,
                    Key=s3_key,
                    Body=conteudo,
                    ContentType="application/pdf",
                    Metadata={"source-url": arquivo.url, "sha256": digest},
                )
            
            with temp_path.open("wb") as destino:
                destino.write(conteudo)
            temp_path.replace(arquivo.destino)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return {
            "source_url": arquivo.url,
            "s3_bucket": S3_BUCKET_NAME if s3_key else None,
            "s3_key": s3_key,
            "sha256": digest,
            "size_bytes": len(conteudo),
            "content_type": content_type or "application/pdf",
        }

    @staticmethod
    def _s3_key(arquivo, run_id, compra):
        orgao_cnpj, ano, numero = compra or ("desconhecido", "desconhecido", "desconhecido")
        sequencial = arquivo.sequencial or "0"
        return "/".join(
            ("runs", str(run_id or "adhoc"), "compras", str(orgao_cnpj), str(ano), str(numero), str(sequencial), arquivo.destino.name)
        )

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

    def _get(self, url, timeout=60, tentativas=5):
        return self.http.get(url, timeout=timeout, retries=tentativas)

    def _json_response(self, response, url):
        try:
            return response.json()
        except ValueError as exc:
            status = getattr(response, "status_code", "desconhecido")
            raise PNCPJsonError(f"JSON invalido em {url} status={status}: {exc}") from exc

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
        return self._classificar_documento(titulo, "", {}) == "priority"

    def _classificar_documento(self, titulo, url, metadados):
        titulo_normalizado = self._normalizar_para_filtro(titulo)
        tem_palavra_alvo = any(self._contem_termo(titulo_normalizado, palavra) for palavra in PALAVRAS_ARQUIVO)
        tem_palavra_forte = any(self._contem_termo(titulo_normalizado, palavra) for palavra in PALAVRAS_ARQUIVO_FORTE)
        tem_exclusao = any(self._contem_termo(titulo_normalizado, palavra) for palavra in PALAVRAS_ARQUIVO_EXCLUIR)

        if not self._parece_pdf(titulo, url, metadados):
            return "ignored"
        if tem_palavra_forte or (tem_palavra_alvo and not tem_exclusao):
            return "priority"
        if tem_exclusao:
            return "ignored"
        return "fallback"

    def _parece_pdf(self, titulo, url, metadados):
        texto = " ".join(
            [
                str(titulo),
                str(url),
                str(metadados.get("tipoDocumentoNome", "")),
                str(metadados.get("tipoDocumento", "")),
                str(metadados.get("nomeArquivo", "")),
            ]
        ).lower()
        extensoes_nao_pdf = (".zip", ".rar", ".7z", ".xlsx", ".xls", ".csv", ".doc", ".docx", ".xml")
        if any(extensao in texto for extensao in extensoes_nao_pdf):
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
