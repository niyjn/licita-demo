import re
import shutil
import subprocess
import time
from pathlib import Path

from pncp_query.config import (
    OCR_DPI,
    OCR_MAX_PAGES,
    PDF_TEXT_MAX_PAGES,
    PDF_TEXT_TIMEOUT_SECONDS,
)
from pncp_query.models import ResultadoPDF

CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
PAGES_RE = re.compile(r"^Pages:\s+(\d+)", re.MULTILINE)


class PDFParserService:
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

        resultado.cnpjs_total = sorted(set(CNPJ_RE.findall(texto)))
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
