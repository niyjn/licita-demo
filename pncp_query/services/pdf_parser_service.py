import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

from pncp_query.config import (
    OCR_DPI,
    OCR_MAX_PAGES,
    PADROES_VENCEDOR,
    PDF_TEXT_MAX_PAGES,
    PDF_TEXT_TIMEOUT_SECONDS,
)
from pncp_query.models import ResultadoPDF
from pncp_query.services.qualification_service import QualificationService

CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
CNPJ_CONTEXT_RE = re.compile(r"cnpj\s*[:\-]?\s*(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})", re.I)
PAGES_RE = re.compile(r"^Pages:\s+(\d+)", re.MULTILINE)
FIRST_PLACE_RE = re.compile(r"(^|\s)1\s+.+?(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})")
NORMALIZE_RE = re.compile(r"[^a-z0-9./-]+")


class PDFParserService:
    def __init__(self):
        self.qualifier = QualificationService()

    def extrair_resultado(self, caminho_pdf: Path):
        inicio = time.perf_counter()
        resultado = ResultadoPDF(arquivo=str(caminho_pdf))
        try:
            texto, page_count = self._extrair_texto_nativo(caminho_pdf)
            resultado.page_count = page_count
            if len(texto.strip()) < 80:
                resultado.origem_texto = "ocr"
                resultado.ocr_attempted = True
                try:
                    texto = self._extrair_texto_ocr(caminho_pdf, page_count)
                    resultado.ocr_success = True
                except Exception as exc:
                    resultado.ocr_error = str(exc)
                    raise
        except Exception as exc:
            resultado.erro = str(exc)
            resultado.parse_duration_ms = int((time.perf_counter() - inicio) * 1000)
            return resultado

        qualificacao = self.qualifier.qualificar_ti(f"{caminho_pdf.name}\n{texto}")
        resultado.qualificado_ti = qualificacao["qualificado"]
        resultado.motivos_qualificacao = qualificacao["inclusoes"]
        resultado.motivos_exclusao = qualificacao["exclusoes"]

        todos = sorted(set(CNPJ_RE.findall(texto)))
        vencedores = sorted(set(self._detectar_vencedores(texto, todos, caminho_pdf.name)))
        derrotados = []
        if resultado.qualificado_ti:
            derrotados = sorted(set(cnpj for cnpj in todos if cnpj not in vencedores))

        resultado.cnpjs_total = todos
        resultado.cnpjs_vencedores = vencedores
        resultado.cnpjs_derrotados = derrotados
        resultado.parse_duration_ms = int((time.perf_counter() - inicio) * 1000)
        return resultado

    def _extrair_texto_nativo(self, caminho_pdf: Path):
        if shutil.which("pdftotext"):
            try:
                return self._extrair_texto_poppler(caminho_pdf)
            except subprocess.TimeoutExpired:
                raise
            except Exception:
                pass
        return self._extrair_texto_pdfplumber(caminho_pdf)

    def _extrair_texto_poppler(self, caminho_pdf: Path):
        page_count = self._contar_paginas_poppler(caminho_pdf)
        limite_paginas = (
            min(page_count, PDF_TEXT_MAX_PAGES)
            if page_count and PDF_TEXT_MAX_PAGES > 0
            else PDF_TEXT_MAX_PAGES
        )
        comando = ["pdftotext", "-layout", "-enc", "UTF-8"]
        if limite_paginas and limite_paginas > 0:
            comando.extend(["-f", "1", "-l", str(limite_paginas)])
        comando.extend([str(caminho_pdf), "-"])
        resultado = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PDF_TEXT_TIMEOUT_SECONDS,
            check=False,
        )
        if resultado.returncode != 0:
            detalhe = (resultado.stderr or resultado.stdout or "").strip()
            raise RuntimeError(f"pdftotext falhou: {detalhe}")
        return resultado.stdout, page_count

    def _contar_paginas_poppler(self, caminho_pdf: Path):
        if not shutil.which("pdfinfo"):
            return 0
        resultado = subprocess.run(
            ["pdfinfo", str(caminho_pdf)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PDF_TEXT_TIMEOUT_SECONDS,
            check=False,
        )
        if resultado.returncode != 0:
            return 0
        match = PAGES_RE.search(resultado.stdout)
        return int(match.group(1)) if match else 0

    def _extrair_texto_pdfplumber(self, caminho_pdf: Path):
        try:
            import pdfplumber
        except ImportError as exc:
            raise RuntimeError("Instale pdfplumber para extrair texto nativo de PDFs.") from exc

        partes = []
        with pdfplumber.open(caminho_pdf) as pdf:
            page_count = len(pdf.pages)
            limite_paginas = min(page_count, PDF_TEXT_MAX_PAGES) if PDF_TEXT_MAX_PAGES > 0 else page_count
            for pagina in pdf.pages[:limite_paginas]:
                try:
                    partes.append(pagina.extract_text() or "")
                finally:
                    flush_cache = getattr(pagina, "flush_cache", None)
                    if flush_cache:
                        flush_cache()
        return "\n".join(partes), page_count

    def _extrair_texto_ocr(self, caminho_pdf: Path, page_count=None):
        try:
            import pytesseract
            from pdf2image import convert_from_path
        except ImportError as exc:
            raise RuntimeError("Instale pdf2image e pytesseract para usar OCR.") from exc

        texto = []
        total_paginas = page_count or 1
        paginas_para_ocr = min(total_paginas, OCR_MAX_PAGES) if OCR_MAX_PAGES > 0 else total_paginas
        for numero_pagina in range(1, paginas_para_ocr + 1):
            imagens = convert_from_path(
                str(caminho_pdf),
                dpi=OCR_DPI,
                first_page=numero_pagina,
                last_page=numero_pagina,
                thread_count=1,
            )
            for imagem in imagens:
                try:
                    texto.append(pytesseract.image_to_string(imagem, lang="por"))
                finally:
                    close = getattr(imagem, "close", None)
                    if close:
                        close()
        return "\n".join(texto)

    def _detectar_vencedores(self, texto, cnpjs, nome_arquivo=""):
        contexto_normalizado = self._normalizar(f"{nome_arquivo}\n{texto}")
        texto_lower = texto.lower()
        vencedores = set()

        if len(cnpjs) == 1 and any(self._normalizar(padrao) in contexto_normalizado for padrao in PADROES_VENCEDOR):
            vencedores.add(cnpjs[0])

        vencedores.update(self._detectar_primeiro_colocado(texto))

        for match in CNPJ_CONTEXT_RE.finditer(texto):
            inicio = max(0, match.start() - 180)
            fim = min(len(texto), match.end() + 220)
            janela = self._normalizar(texto_lower[inicio:fim])
            if any(self._normalizar(padrao) in janela for padrao in PADROES_VENCEDOR) or "melhor proposta" in janela:
                vencedores.add(match.group(1))

        for padrao in PADROES_VENCEDOR:
            padrao_normalizado = self._normalizar(padrao)
            for match in re.finditer(re.escape(padrao_normalizado), contexto_normalizado):
                inicio = max(0, match.start() - 220)
                fim = min(len(contexto_normalizado), match.end() + 220)
                candidatos = list(CNPJ_RE.finditer(contexto_normalizado[inicio:fim]))
                if not candidatos:
                    continue

                posicao_padrao = match.start() - inicio
                mais_proximo = min(
                    candidatos,
                    key=lambda candidato: min(
                        abs(candidato.start() - posicao_padrao),
                        abs(candidato.end() - posicao_padrao),
                    ),
                )
                vencedores.add(mais_proximo.group(0))
        return vencedores

    def _detectar_primeiro_colocado(self, texto):
        vencedores = set()
        texto_normalizado = self._normalizar(texto)
        for header in (
            "posicao fornecedor cpf/cnpj lance final",
            "posicao fornecedor cpf cnpj lance final",
            "lista de classificacao",
        ):
            inicio = texto_normalizado.find(header)
            if inicio == -1:
                continue
            trecho = texto_normalizado[inicio : inicio + 1800]
            match = FIRST_PLACE_RE.search(trecho)
            if match:
                vencedores.add(match.group(2))
        return vencedores

    def _normalizar(self, valor):
        sem_acento = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode("ascii")
        return NORMALIZE_RE.sub(" ", sem_acento.lower()).strip()
