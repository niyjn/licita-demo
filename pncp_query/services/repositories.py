import csv
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from pncp_query.config import (
    PDF_RETENTION_DAYS,
    RUN_LIMIT_DEFAULT,
    RUN_LIMIT_MAX,
    RUN_LIMIT_MIN,
    TARGET_WEEKLY_LEADS,
)
from pncp_query.services.common import somente_digitos
from pncp_query.services.database_service import DatabaseService
from pncp_query.services.lead_candidate_filter import LeadCandidateFilter

STAGES = ("search", "download", "parse", "cleanup", "submit")
BATCH_SIZE = 100

SEARCH_COLUMNS = [
    "termo_busca",
    "status_busca",
    "tipo_documento_busca",
    "numero_controle_pncp",
    "orgao_cnpj",
    "ano",
    "numero_sequencial",
    "orgao_nome",
    "uf",
    "municipio_nome",
    "modalidade_licitacao_nome",
    "situacao_nome",
    "valor_global",
    "data_publicacao_pncp",
    "data_atualizacao_pncp",
    "title",
    "description",
    "item_url",
]


def _json(value):
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        return json.dumps(value, ensure_ascii=False)
    return Jsonb(value)


class RunRepository:
    def __init__(self, db: DatabaseService | None = None):
        self.db = db or DatabaseService()

    def create_run(self, run_id, data_inicial, data_final, limite, limite_origem, args=None, segmento="ti"):
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, segmento, status, data_inicial, data_final, limite_usado,
                    limite_origem, target_weekly_leads, args
                )
                VALUES (%s, %s, 'RUNNING', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status = 'RUNNING',
                    updated_at = now(),
                    limite_usado = EXCLUDED.limite_usado,
                    limite_origem = EXCLUDED.limite_origem,
                    args = EXCLUDED.args
                """,
                (
                    run_id,
                    segmento,
                    data_inicial,
                    data_final,
                    limite,
                    limite_origem,
                    TARGET_WEEKLY_LEADS,
                    _json(args or {}),
                ),
            )
            for stage in STAGES:
                conn.execute(
                    """
                    INSERT INTO pipeline_stages (run_id, stage, status)
                    VALUES (%s, %s, 'PENDING')
                    ON CONFLICT (run_id, stage) DO NOTHING
                    """,
                    (run_id, stage),
                )
            conn.commit()

    def get_run(self, run_id):
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM pipeline_runs WHERE run_id = %s", (run_id,)).fetchone()

    def stage_status(self, run_id, stage):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM pipeline_stages WHERE run_id = %s AND stage = %s",
                (run_id, stage),
            ).fetchone()
            return row["status"] if row else "PENDING"

    def start_stage(self, run_id, stage):
        self._set_stage(run_id, stage, "RUNNING", started=True)

    def complete_stage(self, run_id, stage):
        self._set_stage(run_id, stage, "COMPLETED", completed=True)

    def skip_stage(self, run_id, stage):
        self._set_stage(run_id, stage, "SKIPPED", completed=True)

    def fail_stage(self, run_id, stage, exc):
        self._set_stage(run_id, stage, "FAILED", error_message=str(exc))
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'FAILED', error_count = error_count + 1, updated_at = now()
                WHERE run_id = %s
                """,
                (run_id,),
            )
            conn.commit()

    def complete_run(self, run_id):
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'COMPLETED', completed_at = now(), updated_at = now()
                WHERE run_id = %s
                """,
                (run_id,),
            )
            conn.commit()

    def update_counts(self, run_id, **counts):
        allowed = {
            "licitacoes_persistidas",
            "compras_qualificadas",
            "pdfs_baixados",
            "pdfs_processados",
            "cnpjs_derrotados_brutos",
            "cnpjs_finais_unicos",
            "rate_limit_count",
        }
        updates = [(key, value) for key, value in counts.items() if key in allowed]
        if not updates:
            return
        set_sql = ", ".join(f"{key} = %s" for key, _ in updates)
        params = [value for _, value in updates]
        params.append(run_id)
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE pipeline_runs SET {set_sql}, updated_at = now() WHERE run_id = %s",
                params,
            )
            conn.commit()

    def suggest_limit(self):
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT licitacoes_persistidas, cnpjs_finais_unicos, rate_limit_count
                FROM pipeline_runs
                WHERE status = 'COMPLETED'
                  AND licitacoes_persistidas > 0
                ORDER BY completed_at DESC
                LIMIT 4
                """
            ).fetchall()
        if not rows:
            return RUN_LIMIT_DEFAULT, "default_sem_historico"

        total_leads = sum(int(row["cnpjs_finais_unicos"] or 0) for row in rows)
        total_licitacoes = sum(int(row["licitacoes_persistidas"] or 0) for row in rows)
        total_rate_limit = sum(int(row["rate_limit_count"] or 0) for row in rows)
        if total_leads <= 0 or total_licitacoes <= 0:
            return RUN_LIMIT_DEFAULT, "default_historico_insuficiente"

        taxa = total_leads / total_licitacoes
        estimado = math.ceil(TARGET_WEEKLY_LEADS / taxa)
        if total_rate_limit:
            estimado = math.floor(estimado * 0.85)
        limite = max(RUN_LIMIT_MIN, min(RUN_LIMIT_MAX, estimado))
        return limite, "estatistico_ultimas_4_execucoes"

    def _set_stage(self, run_id, stage, status, started=False, completed=False, error_message=None):
        started_sql = "started_at = COALESCE(started_at, now())," if started else ""
        completed_sql = "completed_at = now()," if completed else ""
        with self.db.connect() as conn:
            conn.execute(
                f"""
                UPDATE pipeline_stages
                SET status = %s,
                    {started_sql}
                    {completed_sql}
                    error_message = %s,
                    updated_at = now()
                WHERE run_id = %s AND stage = %s
                """,
                (status, error_message, run_id, stage),
            )
            conn.commit()


class MetricsRepository:
    def __init__(self, db: DatabaseService | None = None):
        self.db = db or DatabaseService()

    def log(self, run_id, stage, message, level="INFO", payload=None):
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_logs (run_id, stage, level, message, payload)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (run_id, stage, level, message, _json(payload or {})),
            )
            conn.commit()

    def set_metric(self, run_id, key, value):
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_metrics (run_id, metric_key, metric_value)
                VALUES (%s, %s, %s)
                ON CONFLICT (run_id, metric_key) DO UPDATE SET
                    metric_value = EXCLUDED.metric_value,
                    updated_at = now()
                """,
                (run_id, key, _json(value)),
            )
            conn.commit()


class SearchRepository:
    def __init__(self, db: DatabaseService | None = None):
        self.db = db or DatabaseService()

    def save_result(self, run_id, linha):
        return self.save_results(run_id, [linha])

    def save_results(self, run_id, linhas):
        sql = f"""
            INSERT INTO search_results ({", ".join(["run_id", *SEARCH_COLUMNS, "payload"])})
            VALUES ({", ".join(["%s"] * (len(SEARCH_COLUMNS) + 2))})
            ON CONFLICT (run_id, numero_controle_pncp, orgao_cnpj, ano, numero_sequencial) DO NOTHING
        """
        params = (
            [run_id, *[linha.get(coluna, "") for coluna in SEARCH_COLUMNS], _json(linha)]
            for linha in linhas
        )
        return self.db.execute_batch(sql, params, BATCH_SIZE)

    def get_pending_downloads(self, run_id, batch_size=BATCH_SIZE):
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {", ".join("sr." + coluna for coluna in SEARCH_COLUMNS)}
                    FROM search_results sr
                    LEFT JOIN pncp_purchases pp
                      ON pp.run_id = sr.run_id
                     AND pp.source_orgao_cnpj = regexp_replace(COALESCE(sr.orgao_cnpj, ''), '\\D', '', 'g')
                     AND pp.source_ano = sr.ano
                     AND pp.source_numero_sequencial = sr.numero_sequencial
                    WHERE sr.run_id = %s
                      AND pp.id IS NULL
                    ORDER BY sr.id
                    """,
                    (run_id,),
                )
                while True:
                    rows = cur.fetchmany(batch_size)
                    if not rows:
                        break
                    yield from rows

    def count_for_run(self, run_id):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM search_results WHERE run_id = %s",
                (run_id,),
            ).fetchone()
            return int(row["total"] or 0)

    def export_csv(self, run_id, destino: Path):
        destino.parent.mkdir(parents=True, exist_ok=True)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {", ".join(SEARCH_COLUMNS)}
                FROM search_results
                WHERE run_id = %s
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        with destino.open("w", encoding="utf-8", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=SEARCH_COLUMNS, delimiter=";")
            escritor.writeheader()
            escritor.writerows(rows)
        return len(rows)


class DocumentRepository:
    def __init__(self, db: DatabaseService | None = None):
        self.db = db or DatabaseService()

    def save_purchase(self, run_id, chaves, linha, qualificacao=None, status="RESOLVED", error_message=None):
        return self.save_purchases(
            run_id,
            [
                {
                    "chaves": chaves,
                    "linha": linha,
                    "qualificacao": qualificacao or {},
                    "status": status,
                    "error_message": error_message,
                }
            ],
        )

    def save_purchases(self, run_id, purchases):
        sql = """
            INSERT INTO pncp_purchases (
                run_id, orgao_cnpj, ano, numero_sequencial, source_orgao_cnpj,
                source_ano, source_numero_sequencial, qualificado,
                motivos_qualificacao, motivos_exclusao, status, error_message
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, orgao_cnpj, ano, numero_sequencial) DO UPDATE SET
                qualificado = EXCLUDED.qualificado,
                motivos_qualificacao = EXCLUDED.motivos_qualificacao,
                motivos_exclusao = EXCLUDED.motivos_exclusao,
                status = EXCLUDED.status,
                error_message = EXCLUDED.error_message,
                updated_at = now()
        """

        def params():
            for purchase in purchases:
                chaves = purchase["chaves"]
                linha = purchase.get("linha", {})
                qualificacao = purchase.get("qualificacao") or {}
                yield (
                    run_id,
                    chaves[0],
                    chaves[1],
                    chaves[2],
                    somente_digitos(linha.get("orgao_cnpj")),
                    linha.get("ano"),
                    linha.get("numero_sequencial"),
                    bool(qualificacao.get("qualificado", False)),
                    _json(qualificacao.get("inclusoes", [])),
                    _json(qualificacao.get("exclusoes", [])),
                    purchase.get("status", "RESOLVED"),
                    purchase.get("error_message"),
                )

        return self.db.execute_batch(sql, params(), BATCH_SIZE)

    def save_document(self, run_id, chaves, arquivo, status="FOUND", downloaded=False, error_message=None):
        return self.save_documents(
            run_id,
            [
                {
                    "chaves": chaves,
                    "arquivo": arquivo,
                    "status": status,
                    "downloaded": downloaded,
                    "error_message": error_message,
                }
            ],
        )

    def save_documents(self, run_id, documents):
        sql = """
            INSERT INTO documents (
                run_id, purchase_key, titulo, url, file_path, status, downloaded_at,
                error_message, content_sha256, file_size_bytes, content_type,
                magic_type, parent_document_id, extracted_from_zip
            )
            VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, file_path) DO UPDATE SET
                status = EXCLUDED.status,
                downloaded_at = COALESCE(documents.downloaded_at, EXCLUDED.downloaded_at),
                error_message = EXCLUDED.error_message,
                content_sha256 = EXCLUDED.content_sha256,
                file_size_bytes = EXCLUDED.file_size_bytes,
                content_type = EXCLUDED.content_type,
                magic_type = EXCLUDED.magic_type,
                parent_document_id = EXCLUDED.parent_document_id,
                extracted_from_zip = EXCLUDED.extracted_from_zip,
                updated_at = now()
        """

        def params():
            for document in documents:
                chaves = document.get("chaves")
                arquivo = document["arquivo"]
                purchase_key = "/".join(str(parte) for parte in chaves) if chaves else ""
                yield (
                    run_id,
                    purchase_key,
                    arquivo.titulo,
                    arquivo.url,
                    str(arquivo.destino),
                    document.get("status", "FOUND"),
                    bool(document.get("downloaded", False)),
                    document.get("error_message"),
                    document.get("content_sha256"),
                    document.get("file_size_bytes"),
                    document.get("content_type"),
                    document.get("magic_type"),
                    document.get("parent_document_id"),
                    bool(document.get("extracted_from_zip", False)),
                )

        return self.db.execute_batch(sql, params(), BATCH_SIZE)

    def get_pending_parse(self, run_id, batch_size=BATCH_SIZE, include_processed=False):
        where_processed = "" if include_processed else "AND ppr.id IS NULL"
        status_filter = (
            "d.status IN ('DOWNLOADED', 'PARSED', 'FAILED', 'INVALID_FILE', 'ZIP_EMPTY')"
            if include_processed
            else "d.status IN ('DOWNLOADED', 'INVALID_FILE', 'ZIP_EMPTY')"
        )
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT d.id, d.file_path, d.purchase_key, d.titulo, d.url, d.content_sha256, d.magic_type
                    FROM documents d
                    LEFT JOIN pdf_parse_results ppr
                      ON ppr.run_id = d.run_id
                     AND ppr.file_path = d.file_path
                    WHERE d.run_id = %s
                      AND {status_filter}
                      AND d.file_deleted_at IS NULL
                      {where_processed}
                    ORDER BY d.id
                    """,
                    (run_id,),
                )
                while True:
                    rows = cur.fetchmany(batch_size)
                    if not rows:
                        break
                    yield from rows

    def get_document_id(self, run_id, file_path):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM documents WHERE run_id = %s AND file_path = %s",
                (run_id, str(file_path)),
            ).fetchone()
            return row["id"] if row else None

    def existing_hashes(self, hashes):
        valores = [valor for valor in hashes if valor]
        if not valores:
            return set()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT content_sha256 FROM documents WHERE content_sha256 = ANY(%s)",
                (valores,),
            ).fetchall()
            return {row["content_sha256"] for row in rows}

    def copy_parse_result_from_hash(self, run_id, file_path, content_sha256):
        if not content_sha256:
            return False
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT ppr.*
                FROM pdf_parse_results ppr
                JOIN documents d
                  ON d.run_id = ppr.run_id
                 AND d.file_path = ppr.file_path
                WHERE d.content_sha256 = %s
                  AND ppr.erro = ''
                ORDER BY ppr.created_at DESC
                LIMIT 1
                """,
                (content_sha256,),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                """
                INSERT INTO pdf_parse_results (
                    run_id, file_path, cnpjs_total, cnpjs_vencedores, cnpjs_derrotados,
                    qualificado_ti, motivos_qualificacao, motivos_exclusao, origem_texto, erro,
                    ocr_attempted, ocr_success, ocr_error, page_count, parse_duration_ms
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '', %s, %s, %s, %s, 0)
                ON CONFLICT (run_id, file_path) DO NOTHING
                """,
                (
                    run_id,
                    str(file_path),
                    _json(row["cnpjs_total"] or []),
                    _json(row["cnpjs_vencedores"] or []),
                    _json(row["cnpjs_derrotados"] or []),
                    bool(row["qualificado_ti"]),
                    _json(row["motivos_qualificacao"] or []),
                    _json(row["motivos_exclusao"] or []),
                    row["origem_texto"],
                    bool(row["ocr_attempted"]),
                    bool(row["ocr_success"]),
                    row["ocr_error"],
                    row["page_count"],
                ),
            )
            conn.execute(
                """
                UPDATE documents
                SET parsed_at = now(), status = 'PARSED', updated_at = now()
                WHERE run_id = %s AND file_path = %s
                """,
                (run_id, str(file_path)),
            )
            conn.commit()
            return True

    def processed_pdf_paths(self, run_id):
        with self.db.connect() as conn:
            rows = conn.execute("SELECT file_path FROM pdf_parse_results WHERE run_id = %s", (run_id,)).fetchall()
            return {row["file_path"] for row in rows}

    def count_qualified_purchases(self, run_id):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM pncp_purchases WHERE run_id = %s AND qualificado = true",
                (run_id,),
            ).fetchone()
            return int(row["total"] or 0)

    def count_parsed_pdfs(self, run_id):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM pdf_parse_results WHERE run_id = %s",
                (run_id,),
            ).fetchone()
            return int(row["total"] or 0)

    def save_parse_result(self, run_id, resultado):
        return self.save_parse_results(run_id, [resultado])

    def save_parse_results(self, run_id, resultados):
        rows = [resultado if isinstance(resultado, dict) else resultado.__dict__ for resultado in resultados]
        if not rows:
            return 0
        insert_sql = """
            INSERT INTO pdf_parse_results (
                run_id, file_path, cnpjs_total, cnpjs_vencedores, cnpjs_derrotados,
                qualificado_ti, motivos_qualificacao, motivos_exclusao, origem_texto, erro,
                ocr_attempted, ocr_success, ocr_error, page_count, parse_duration_ms
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, file_path) DO UPDATE SET
                cnpjs_total = EXCLUDED.cnpjs_total,
                cnpjs_vencedores = EXCLUDED.cnpjs_vencedores,
                cnpjs_derrotados = EXCLUDED.cnpjs_derrotados,
                qualificado_ti = EXCLUDED.qualificado_ti,
                motivos_qualificacao = EXCLUDED.motivos_qualificacao,
                motivos_exclusao = EXCLUDED.motivos_exclusao,
                origem_texto = EXCLUDED.origem_texto,
                erro = EXCLUDED.erro,
                ocr_attempted = EXCLUDED.ocr_attempted,
                ocr_success = EXCLUDED.ocr_success,
                ocr_error = EXCLUDED.ocr_error,
                page_count = EXCLUDED.page_count,
                parse_duration_ms = EXCLUDED.parse_duration_ms
        """
        insert_params = (
            (
                run_id,
                row.get("arquivo"),
                _json(row.get("cnpjs_total", [])),
                _json(row.get("cnpjs_vencedores", [])),
                _json(row.get("cnpjs_derrotados", [])),
                bool(row.get("qualificado_ti", False)),
                _json(row.get("motivos_qualificacao", [])),
                _json(row.get("motivos_exclusao", [])),
                row.get("origem_texto"),
                row.get("erro"),
                bool(row.get("ocr_attempted", False)),
                bool(row.get("ocr_success", False)),
                row.get("ocr_error"),
                row.get("page_count") or 0,
                row.get("parse_duration_ms") or 0,
            )
            for row in rows
        )
        total = self.db.execute_batch(insert_sql, insert_params, BATCH_SIZE)
        update_sql = """
            UPDATE documents
            SET parsed_at = now(),
                status = CASE WHEN %s = '' THEN 'PARSED' ELSE 'FAILED' END,
                updated_at = now()
            WHERE run_id = %s AND file_path = %s
        """
        update_params = ((row.get("erro") or "", run_id, row.get("arquivo")) for row in rows)
        self.db.execute_batch(update_sql, update_params, BATCH_SIZE)
        return total

    def cleanup_old_pdfs(self, retention_days=PDF_RETENTION_DAYS):
        limite = datetime.now() - timedelta(days=retention_days)
        removidos = []
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, file_path
                FROM documents
                WHERE downloaded_at IS NOT NULL
                  AND downloaded_at < %s
                  AND file_deleted_at IS NULL
                """,
                (limite,),
            ).fetchall()
            for row in rows:
                caminho = Path(row["file_path"])
                if not caminho.exists():
                    conn.execute(
                        "UPDATE documents SET file_deleted_at = now(), updated_at = now() WHERE id = %s",
                        (row["id"],),
                    )
                    continue
                caminho.unlink()
                removidos.append(str(caminho))
                conn.execute(
                    "UPDATE documents SET file_deleted_at = now(), updated_at = now() WHERE id = %s",
                    (row["id"],),
                )
            conn.commit()
        return removidos


class LeadRepository:
    def __init__(self, db: DatabaseService | None = None, candidate_filter=None):
        self.db = db or DatabaseService()
        self.candidate_filter = candidate_filter or LeadCandidateFilter()

    def reset_for_run(self, run_id, segmento="ti"):
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM lead_candidates WHERE run_id = %s AND segmento = %s",
                (run_id, segmento),
            )
            conn.execute(
                "DELETE FROM rejected_lead_candidates WHERE run_id = %s AND segmento = %s",
                (run_id, segmento),
            )
            conn.commit()

    def _insert_candidates(self, candidates):
        sql = """
            INSERT INTO lead_candidates (run_id, segmento, cnpj, source_file_path, status)
            VALUES (%s, %s, %s, %s, 'READY_TO_EXPORT')
            ON CONFLICT (run_id, segmento, cnpj) DO NOTHING
        """
        return self.db.execute_batch(sql, candidates, BATCH_SIZE)

    def _insert_rejections(self, rejections):
        sql = """
            INSERT INTO rejected_lead_candidates (run_id, segmento, cnpj, source_file_path, reason, details)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, segmento, cnpj, source_file_path, reason) DO NOTHING
        """
        params = (
            (run_id, segmento, cnpj, source_file_path, reason, _json(details or {}))
            for run_id, segmento, cnpj, source_file_path, reason, details in rejections
        )
        return self.db.execute_batch(sql, params, BATCH_SIZE)

    def save_candidates_from_results(self, run_id, resultados, segmento="ti"):
        self.reset_for_run(run_id, segmento)
        total_brutos = 0
        batch = []
        rejeitados = []
        for resultado in resultados:
            if not resultado.get("qualificado_ti", False):
                continue
            for cnpj in resultado.get("cnpjs_derrotados", []):
                total_brutos += 1
                decision = self.candidate_filter.evaluate(cnpj)
                if decision.accepted:
                    batch.append((run_id, segmento, decision.cnpj, resultado.get("arquivo")))
                else:
                    rejeitados.append(
                        (run_id, segmento, decision.cnpj, resultado.get("arquivo"), decision.reason, decision.details)
                    )
                if len(batch) >= BATCH_SIZE:
                    self._insert_candidates(batch)
                    batch.clear()
                if len(rejeitados) >= BATCH_SIZE:
                    self._insert_rejections(rejeitados)
                    rejeitados.clear()
        if batch:
            self._insert_candidates(batch)
        if rejeitados:
            self._insert_rejections(rejeitados)
        return total_brutos

    def save_candidates_from_db_results(self, run_id, segmento="ti"):
        self.reset_for_run(run_id, segmento)
        total_brutos = 0
        batch = []
        rejeitados = []
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        ppr.file_path,
                        ppr.cnpjs_derrotados,
                        pp.orgao_cnpj,
                        pp.source_orgao_cnpj
                    FROM pdf_parse_results ppr
                    LEFT JOIN documents d
                      ON d.run_id = ppr.run_id
                     AND d.file_path = ppr.file_path
                    LEFT JOIN pncp_purchases pp
                      ON pp.run_id = d.run_id
                     AND pp.orgao_cnpj = split_part(d.purchase_key, '/', 1)
                     AND pp.ano = split_part(d.purchase_key, '/', 2)
                     AND pp.numero_sequencial = split_part(d.purchase_key, '/', 3)
                    WHERE ppr.run_id = %s
                      AND ppr.qualificado_ti = true
                    ORDER BY ppr.id
                    """,
                    (run_id,),
                )
                while True:
                    rows = cur.fetchmany(BATCH_SIZE)
                    if not rows:
                        break
                    for row in rows:
                        cnpjs = row["cnpjs_derrotados"] or []
                        total_brutos += len(cnpjs)
                        for cnpj in cnpjs:
                            decision = self.candidate_filter.evaluate(
                                cnpj,
                                buyer_org_cnpj=row["orgao_cnpj"],
                                source_org_cnpj=row["source_orgao_cnpj"],
                            )
                            if decision.accepted:
                                batch.append((run_id, segmento, decision.cnpj, row["file_path"]))
                            else:
                                rejeitados.append(
                                    (
                                        run_id,
                                        segmento,
                                        decision.cnpj,
                                        row["file_path"],
                                        decision.reason,
                                        decision.details,
                                    )
                                )
                            if len(batch) >= BATCH_SIZE:
                                self._insert_candidates(batch)
                                batch.clear()
                            if len(rejeitados) >= BATCH_SIZE:
                                self._insert_rejections(rejeitados)
                                rejeitados.clear()
        if batch:
            self._insert_candidates(batch)
        if rejeitados:
            self._insert_rejections(rejeitados)
        return total_brutos

    def count_rejections_for_run(self, run_id, segmento="ti"):
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM rejected_lead_candidates
                WHERE run_id = %s AND segmento = %s
                """,
                (run_id, segmento),
            ).fetchone()
            return int(row["total"] or 0)

    def count_rejections_by_reason(self, run_id, segmento="ti"):
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT reason, COUNT(*) AS total
                FROM rejected_lead_candidates
                WHERE run_id = %s AND segmento = %s
                GROUP BY reason
                ORDER BY reason
                """,
                (run_id, segmento),
            ).fetchall()
            return {row["reason"]: int(row["total"] or 0) for row in rows}

    def get_ready_to_export(self, run_id, segmento="ti", batch_size=BATCH_SIZE):
        with self.db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT cnpj
                    FROM lead_candidates
                    WHERE run_id = %s AND segmento = %s AND status = 'READY_TO_EXPORT'
                    ORDER BY cnpj
                    """,
                    (run_id, segmento),
                )
                while True:
                    rows = cur.fetchmany(batch_size)
                    if not rows:
                        break
                    yield from rows

    def export_csv(self, run_id, destino: Path, segmento="ti"):
        destino.parent.mkdir(parents=True, exist_ok=True)
        cnpjs = []
        with destino.open("w", encoding="utf-8", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=["cnpj"], delimiter=";")
            escritor.writeheader()
            for row in self.get_ready_to_export(run_id, segmento):
                escritor.writerow({"cnpj": row["cnpj"]})
                cnpjs.append(row["cnpj"])
        return cnpjs

    def count_for_run(self, run_id, segmento="ti"):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM lead_candidates WHERE run_id = %s AND segmento = %s",
                (run_id, segmento),
            ).fetchone()
            return int(row["total"] or 0)
