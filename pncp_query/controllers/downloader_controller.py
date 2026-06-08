import time

from pncp_query.config import LICITACOES_CSV, PDF_DIR
from pncp_query.models import ArquivoPNCP
from pncp_query.services.csv_repository import ler_csv
from pncp_query.services.downloader_service import DownloaderService
from pncp_query.services.file_inspection_service import FileInspectionService
from pncp_query.services.metrics_service import MetricsService


class DownloaderController:
    def __init__(self, service=None, document_repository=None, search_repository=None):
        self.service = service or DownloaderService()
        self.metrics = MetricsService()
        self.document_repository = document_repository
        self.search_repository = search_repository
        self.file_inspector = FileInspectionService()

    def executar(self, csv_entrada=LICITACOES_CSV, pdf_dir=PDF_DIR, pausa=0.5, logger=print, run_id=None):
        baixados = 0
        compras_processadas = set()
        purchase_batch = []
        document_batch = []
        usar_banco = bool(run_id and self.document_repository and self.search_repository)
        pdf_dir.mkdir(parents=True, exist_ok=True)

        def flush_purchases():
            if purchase_batch:
                self.document_repository.save_purchases(run_id, purchase_batch)
                purchase_batch.clear()

        def flush_documents():
            if document_batch:
                self.document_repository.save_documents(run_id, document_batch)
                document_batch.clear()

        linhas = self.search_repository.get_pending_downloads(run_id) if usar_banco else ler_csv(csv_entrada)
        for linha in linhas:
            self.metrics.increment("downloader_linhas_lidas")
            logger(
                "Listando arquivos: "
                f"{linha.get('orgao_cnpj')}/{linha.get('ano')}/{linha.get('numero_sequencial')}"
            )
            try:
                chaves = self.service.resolver_chaves_compra(linha)
                if not chaves:
                    logger("  [AVISO] Nao foi possivel resolver a compra original deste contrato.")
                    self.metrics.increment("compras_nao_resolvidas")
                    continue
                logger(f"  -> compra original: {chaves[0]}/{chaves[1]}/{chaves[2]}")
                if chaves in compras_processadas:
                    logger("  -> compra ja processada nesta rodada")
                    self.metrics.increment("compras_duplicadas_puladas")
                    continue
                compras_processadas.add(chaves)
                self.metrics.increment("compras_resolvidas")
                qualificacao = self.service.qualificar_compra(linha, chaves)
                if run_id and self.document_repository:
                    purchase_batch.append({"chaves": chaves, "linha": linha, "qualificacao": qualificacao})
                    if len(purchase_batch) >= 100:
                        flush_purchases()
                if not qualificacao["qualificado"]:
                    logger(
                        "  [PULANDO] Fora do foco TI. "
                        f"TI={qualificacao['inclusoes'] or '-'} "
                        f"exclusoes={qualificacao['exclusoes'] or '-'}"
                    )
                    self.metrics.increment("compras_fora_foco_ti")
                    continue
                arquivos = self.service.listar_arquivos_relevantes(linha, pdf_dir, chaves)
            except Exception as exc:
                logger(f"  [ERRO] Falha ao listar arquivos: {exc}")
                self.metrics.increment("erros_downloader")
                self.metrics.add_error("downloader", exc)
                continue

            logger(f"  -> arquivos relevantes: {len(arquivos)}")
            self.metrics.increment("arquivos_relevantes", len(arquivos))
            for arquivo in arquivos:
                try:
                    novo = self.service.baixar(arquivo)
                    baixados += int(novo)
                    if run_id and self.document_repository:
                        documentos = self._documentos_do_arquivo(
                            run_id,
                            chaves,
                            arquivo,
                            pdf_dir,
                            novo,
                            logger,
                            flush_documents,
                        )
                        document_batch.extend(documentos)
                        if len(document_batch) >= 100:
                            flush_documents()
                    status = "salvo" if novo else "ja existe"
                    logger(f"    [OK] {status}: {arquivo.destino}")
                    self.metrics.increment("pdfs_baixados" if novo else "pdfs_ja_existentes")
                except Exception as exc:
                    if run_id and self.document_repository:
                        document_batch.append(
                            {
                                "chaves": chaves,
                                "arquivo": arquivo,
                                "status": "FAILED",
                                "error_message": str(exc),
                            }
                        )
                        if len(document_batch) >= 100:
                            flush_documents()
                    logger(f"    [ERRO] Falha no download {arquivo.url}: {exc}")
                    self.metrics.increment("erros_download_arquivo")
                    self.metrics.add_error("download_arquivo", exc)
            time.sleep(pausa)
        if run_id and self.document_repository:
            flush_purchases()
            flush_documents()
        logger(f"Download concluido. PDFs baixados: {baixados}")
        self.metrics.set("pdfs_baixados_total_ultima_execucao", baixados)
        return baixados

    def _documentos_do_arquivo(self, run_id, chaves, arquivo, pdf_dir, novo, logger, flush_documents):
        inspection = self.file_inspector.inspect(arquivo.destino)
        base = self._document_payload(chaves, arquivo, inspection, downloaded=novo)

        if inspection.magic_type == "PDF":
            base["status"] = "DOWNLOADED"
            return [base]

        if inspection.magic_type != "ZIP":
            base["status"] = "INVALID_FILE"
            base["error_message"] = f"Arquivo nao-PDF detectado: {inspection.magic_type}"
            self.metrics.increment("arquivos_invalidos")
            return [base]

        base["status"] = "ZIP_EXTRACTED"
        documentos = [base]
        self.metrics.increment("arquivos_zip")
        try:
            extraidos = self.file_inspector.extract_zip_pdfs(arquivo.destino, pdf_dir)
        except Exception as exc:
            base["status"] = "FAILED"
            base["error_message"] = f"Falha ao extrair ZIP: {exc}"
            self.metrics.increment("erros_zip")
            return documentos

        if not extraidos:
            base["status"] = "ZIP_EMPTY"
            base["error_message"] = "ZIP sem PDFs internos."
            self.metrics.increment("zips_sem_pdf")
            return documentos

        flush_documents()
        self.document_repository.save_documents(run_id, [base])
        parent_id = self.document_repository.get_document_id(run_id, arquivo.destino)
        documentos.clear()

        for caminho_pdf in extraidos:
            child_inspection = self.file_inspector.inspect(caminho_pdf)
            child = ArquivoPNCP(titulo=f"{arquivo.titulo} :: {caminho_pdf.name}", url=arquivo.url, destino=caminho_pdf)
            payload = self._document_payload(
                chaves,
                child,
                child_inspection,
                downloaded=True,
                parent_document_id=parent_id,
                extracted_from_zip=True,
            )
            payload["status"] = "DOWNLOADED" if child_inspection.magic_type == "PDF" else "INVALID_FILE"
            if payload["status"] == "INVALID_FILE":
                payload["error_message"] = f"PDF extraido invalido: {child_inspection.magic_type}"
            documentos.append(payload)
            self.metrics.increment("pdfs_extraidos_zip")
        logger(f"    [ZIP] PDFs extraidos: {len(documentos)}")
        return documentos

    def _document_payload(
        self,
        chaves,
        arquivo,
        inspection,
        downloaded=False,
        parent_document_id=None,
        extracted_from_zip=False,
    ):
        return {
            "chaves": chaves,
            "arquivo": arquivo,
            "status": "DOWNLOADED",
            "downloaded": downloaded,
            "content_sha256": inspection.sha256,
            "file_size_bytes": inspection.file_size_bytes,
            "content_type": inspection.content_type,
            "magic_type": inspection.magic_type,
            "parent_document_id": parent_document_id,
            "extracted_from_zip": extracted_from_zip,
        }
