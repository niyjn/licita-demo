from dataclasses import asdict
from pathlib import Path

from pncp_query.config import CNPJS_BRUTOS_JSON, PDF_DIR
from pncp_query.models import ResultadoPDF
from pncp_query.services.csv_repository import append_jsonl, salvar_json
from pncp_query.services.metrics_service import MetricsService
from pncp_query.services.pdf_parser_service import PDFParserService


class ParserPDFController:
    def __init__(self, service=None, document_repository=None):
        self.service = service or PDFParserService()
        self.metrics = MetricsService()
        self.document_repository = document_repository

    def executar(self, pdf_dir=PDF_DIR, destino=CNPJS_BRUTOS_JSON, logger=print, run_id=None, force_parse=False):
        pdf_dir = Path(pdf_dir)
        destino = Path(destino)
        jsonl_path = destino.with_suffix(".jsonl")
        usar_banco = bool(run_id and self.document_repository)
        if not usar_banco:
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            jsonl_path.write_text("", encoding="utf-8")
        resultados = []
        processados_count = 0
        cnpjs_derrotados_brutos = 0
        processados = set()
        if usar_banco:
            arquivos = self.document_repository.get_pending_parse(run_id, include_processed=force_parse)
        else:
            arquivos = sorted(pdf_dir.glob("*.pdf"))
        if run_id and self.document_repository and not usar_banco and not force_parse:
            processados = self.document_repository.processed_pdf_paths(run_id)
        for item in arquivos:
            row = item if isinstance(item, dict) else None
            caminho_pdf = Path(row["file_path"] if row else item)
            if str(caminho_pdf) in processados:
                logger(f"Pulando PDF ja processado: {caminho_pdf}")
                continue
            if usar_banco and row and row.get("magic_type") and row.get("magic_type") != "PDF":
                resultado = ResultadoPDF(
                    arquivo=str(caminho_pdf),
                    erro=f"Arquivo nao-PDF detectado: {row['magic_type']}",
                )
                resultado_dict = asdict(resultado)
                processados_count += 1
                self.metrics.increment("pdfs_invalidos")
                self.document_repository.save_parse_results(run_id, [resultado_dict])
                continue
            if (
                usar_banco
                and row
                and not force_parse
                and self.document_repository.copy_parse_result_from_hash(run_id, caminho_pdf, row.get("content_sha256"))
            ):
                logger(f"Reaproveitando parse por hash: {caminho_pdf}")
                processados_count += 1
                self.metrics.increment("pdfs_reaproveitados_hash")
                continue
            self.metrics.increment("pdfs_processados")
            logger(f"Processando PDF: {caminho_pdf}")
            resultado = self.service.extrair_resultado(caminho_pdf)
            if resultado.erro:
                logger(f"  [ERRO] {resultado.erro}")
                self.metrics.increment("pdfs_com_erro")
                self.metrics.add_error("parser_pdf", f"{caminho_pdf}: {resultado.erro}")
            else:
                logger(
                    "  -> "
                    f"total={len(resultado.cnpjs_total)} "
                    f"vencedores={len(resultado.cnpjs_vencedores)} "
                    f"derrotados={len(resultado.cnpjs_derrotados)} "
                    f"ti={resultado.qualificado_ti}"
                )
                if resultado.motivos_exclusao:
                    logger(f"     exclusoes={resultado.motivos_exclusao}")
                self.metrics.increment("cnpjs_encontrados", len(resultado.cnpjs_total))
                self.metrics.increment("cnpjs_vencedores", len(resultado.cnpjs_vencedores))
                self.metrics.increment("cnpjs_derrotados_brutos", len(resultado.cnpjs_derrotados))
                if resultado.qualificado_ti:
                    self.metrics.increment("pdfs_qualificados_ti")
            if resultado.ocr_attempted:
                self.metrics.increment("pdfs_ocr_tentado")
            if resultado.ocr_success:
                self.metrics.increment("pdfs_ocr_sucesso")
            if resultado.ocr_error:
                self.metrics.increment("pdfs_ocr_erro")
            resultado_dict = asdict(resultado)
            processados_count += 1
            cnpjs_derrotados_brutos += len(resultado_dict.get("cnpjs_derrotados", []))
            if usar_banco:
                self.document_repository.save_parse_results(run_id, [resultado_dict])
            else:
                resultados.append(resultado_dict)
                append_jsonl(jsonl_path, resultado_dict)
                if run_id and self.document_repository:
                    self.document_repository.save_parse_result(run_id, resultado_dict)
        if usar_banco:
            logger(f"Resultados de parser persistidos no PostgreSQL: {processados_count}")
            return {"processados": processados_count, "cnpjs_derrotados_brutos": cnpjs_derrotados_brutos}
        salvar_json(destino, resultados)
        logger(f"Relatorio intermediario: {destino}")
        logger(f"JSONL incremental: {jsonl_path}")
        return resultados
