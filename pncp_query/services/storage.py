"""PostgreSQL persistence for analysis runs.

Schema ownership belongs to Alembic.  ``Storage`` only opens pooled connections;
it intentionally contains no DDL, backend detection, or SQL dialect translation.
"""

import json
from contextlib import contextmanager
from datetime import datetime

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from pncp_query.config import DB_POOL_MAX, DB_POOL_MIN, require_database_url


class Storage:
    def __init__(self, database_url=None, *, pool_min=None, pool_max=None):
        self.database_url = database_url or require_database_url()
        minimum = DB_POOL_MIN if pool_min is None else pool_min
        maximum = DB_POOL_MAX if pool_max is None else pool_max
        if minimum < 1 or maximum < minimum:
            raise ValueError("DB_POOL_MIN e DB_POOL_MAX precisam formar um pool válido.")
        self._pool = ThreadedConnectionPool(minimum, maximum, self.database_url)
        self._closed = False

    @contextmanager
    def connect(self):
        if self._closed:
            raise RuntimeError("Storage já foi fechado.")
        connection = self._pool.getconn()
        cursor = connection.cursor(cursor_factory=RealDictCursor)
        try:
            yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            self._pool.putconn(connection)

    def close(self):
        """Release all pooled connections. Safe to call more than once."""
        if not self._closed:
            self._pool.closeall()
            self._closed = True

    closeall = close

    def ping(self):
        with self.connect() as cursor:
            cursor.execute("SELECT 1 AS ok")
            return cursor.fetchone()["ok"] == 1

    def criar_run(self, run_id, params_json="{}", owner_id=None):
        with self.connect() as cursor:
            cursor.execute(
                """INSERT INTO runs (id, status, progress, message, params_json, owner_id, created_at)
                   VALUES (%s, 'queued', 0, 'Análise na fila.', %s, %s, now())""",
                (run_id, params_json, owner_id),
            )
        return run_id

    def criar_run_se_disponivel(self, run_id, params_json="{}", owner_id=None):
        """Queue a run. Multiple queued runs are valid and workers claim atomically."""
        return self.criar_run(run_id, params_json, owner_id)

    def criar_identidade(self, owner_id, hashed_token, expires_at):
        with self.connect() as cursor:
            cursor.execute(
                """INSERT INTO anonymous_identities (id, token_hash, expires_at)
                   VALUES (%s, %s, %s)""",
                (owner_id, hashed_token, expires_at),
            )

    def obter_identidade_por_hash(self, hashed_token):
        with self.connect() as cursor:
            cursor.execute("SELECT * FROM anonymous_identities WHERE token_hash = %s", (hashed_token,))
            row = cursor.fetchone()
            # This private security boundary must retain TIMESTAMPTZ values for
            # expiry comparisons; public run/profile APIs remain JSON-safe.
            return dict(row) if row else None

    def tocar_identidade(self, owner_id, expires_at):
        with self.connect() as cursor:
            cursor.execute(
                "UPDATE anonymous_identities SET last_seen_at = now(), expires_at = %s WHERE id = %s",
                (expires_at, owner_id),
            )

    def atualizar_run(self, run_id, status=None, progress=None, message=None, error=None):
        updates, params = [], []
        if status is not None:
            updates.append("status = %s")
            params.append(status)
            if status == "running":
                updates.append("started_at = COALESCE(started_at, now())")
            if status in {"done", "error"}:
                updates.append("finished_at = now()")
        if progress is not None:
            updates.append("progress = %s")
            params.append(float(progress))
        if message is not None:
            updates.append("message = %s")
            params.append(str(message))
        if error is not None:
            updates.append("error = %s")
            params.append(str(error))
        if not updates:
            return False
        params.append(run_id)
        with self.connect() as cursor:
            cursor.execute(f"UPDATE runs SET {', '.join(updates)} WHERE id = %s", params)
            return cursor.rowcount == 1

    def claim_next_run(self, worker_id):
        """Atomically claim one oldest queued run without blocking peer workers."""
        with self.connect() as cursor:
            cursor.execute(
                """
                WITH next_run AS (
                    SELECT id FROM runs WHERE status = 'queued'
                    ORDER BY created_at ASC, id ASC FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE runs AS target
                SET status = 'running', progress = 0, message = 'Análise iniciada.',
                    started_at = COALESCE(target.started_at, now()), heartbeat_at = now(),
                    worker_id = %s, attempt_count = target.attempt_count + 1
                FROM next_run
                WHERE target.id = next_run.id AND target.status = 'queued'
                RETURNING target.*
                """,
                (worker_id,),
            )
            row = cursor.fetchone()
            return _as_dict(row) if row else None

    def heartbeat_run(self, run_id, worker_id, progress=None, message=None, duration_seconds=None):
        updates, params = ["heartbeat_at = now()"], []
        if progress is not None:
            updates.append("progress = %s")
            params.append(float(progress))
        if message is not None:
            updates.append("message = %s")
            params.append(str(message))
        if duration_seconds is not None:
            updates.append("duration_seconds = %s")
            params.append(int(duration_seconds))
        params.extend((run_id, worker_id))
        with self.connect() as cursor:
            cursor.execute(
                f"UPDATE runs SET {', '.join(updates)} WHERE id = %s AND status = 'running' AND worker_id = %s",
                params,
            )
            return cursor.rowcount == 1

    def _finish_claimed(self, run_id, worker_id, status, message, error, duration_seconds):
        updates = [
            "status = %s",
            "progress = 100",
            "message = %s",
            "error = %s",
            "heartbeat_at = now()",
            "finished_at = now()",
        ]
        params = [status, message, error]
        if duration_seconds is not None:
            updates.append("duration_seconds = %s")
            params.append(int(duration_seconds))
        params.extend((run_id, worker_id))
        with self.connect() as cursor:
            cursor.execute(
                f"UPDATE runs SET {', '.join(updates)} WHERE id = %s AND status = 'running' AND worker_id = %s",
                params,
            )
            return cursor.rowcount == 1

    def complete_claimed_run(self, run_id, worker_id, message="Análise concluída.", duration_seconds=None):
        return self._finish_claimed(run_id, worker_id, "done", message, "", duration_seconds)

    def fail_claimed_run(self, run_id, worker_id, error, message="Análise falhou.", duration_seconds=None):
        return self._finish_claimed(run_id, worker_id, "error", message, str(error), duration_seconds)

    def limpar_runs_travadas(self, timeout_segundos=3600):
        with self.connect() as cursor:
            cursor.execute(
                """UPDATE runs SET status = 'error', message = 'Análise excedeu o tempo limite.',
                   error = 'Timeout de processamento.', finished_at = now(), heartbeat_at = now()
                   WHERE status = 'running'
                     AND COALESCE(heartbeat_at, started_at, created_at) < now() - (%s * interval '1 second')""",
                (int(timeout_segundos),),
            )
            return cursor.rowcount

    def obter_run(self, run_id):
        with self.connect() as cursor:
            cursor.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
            row = cursor.fetchone()
            return _as_dict(row) if row else None

    def obter_run_do_owner(self, run_id, owner_id):
        with self.connect() as cursor:
            cursor.execute("SELECT * FROM runs WHERE id = %s AND owner_id = %s", (run_id, owner_id))
            row = cursor.fetchone()
            return _as_dict(row) if row else None

    def ultima_run(self):
        with self.connect() as cursor:
            cursor.execute("SELECT * FROM runs ORDER BY created_at DESC, id DESC LIMIT 1")
            row = cursor.fetchone()
            return _as_dict(row) if row else None

    def ultima_run_do_owner(self, owner_id):
        with self.connect() as cursor:
            cursor.execute(
                "SELECT * FROM runs WHERE owner_id = %s ORDER BY created_at DESC, id DESC LIMIT 1", (owner_id,)
            )
            row = cursor.fetchone()
            return _as_dict(row) if row else None

    def listar_runs(self, limit=20, offset=0, status=None):
        where, params = "", []
        if status:
            where = "WHERE r.status = %s"
            params.append(status)
        params.extend((max(1, int(limit)), max(0, int(offset))))
        with self.connect() as cursor:
            cursor.execute(
                f"""SELECT r.*, COUNT(c.id) AS contratos_count FROM runs r
                    LEFT JOIN contratos c ON c.run_id = r.id {where}
                    GROUP BY r.id ORDER BY r.created_at DESC, r.id DESC LIMIT %s OFFSET %s""",
                params,
            )
            return [_as_dict(row) for row in cursor.fetchall()]

    def contar_runs(self, status=None):
        query, params = "SELECT COUNT(*) AS total FROM runs", []
        if status:
            query += " WHERE status = %s"
            params.append(status)
        with self.connect() as cursor:
            cursor.execute(query, params)
            return int(cursor.fetchone()["total"])

    def listar_runs_do_owner(self, owner_id, limit=20, offset=0, status=None):
        filters, params = ["r.owner_id = %s"], [owner_id]
        if status:
            filters.append("r.status = %s")
            params.append(status)
        params.extend((max(1, int(limit)), max(0, int(offset))))
        with self.connect() as cursor:
            cursor.execute(
                f"""SELECT r.*, COUNT(c.id) AS contratos_count FROM runs r
                    LEFT JOIN contratos c ON c.run_id = r.id WHERE {" AND ".join(filters)}
                    GROUP BY r.id ORDER BY r.created_at DESC, r.id DESC LIMIT %s OFFSET %s""",
                params,
            )
            return [_as_dict(row) for row in cursor.fetchall()]

    def contar_runs_do_owner(self, owner_id, status=None):
        query, params = "SELECT COUNT(*) AS total FROM runs WHERE owner_id = %s", [owner_id]
        if status:
            query += " AND status = %s"
            params.append(status)
        with self.connect() as cursor:
            cursor.execute(query, params)
            return int(cursor.fetchone()["total"])

    def excluir_run(self, run_id):
        with self.connect() as cursor:
            cursor.execute("DELETE FROM runs WHERE id = %s", (run_id,))
            return cursor.rowcount > 0

    def excluir_run_do_owner(self, run_id, owner_id):
        with self.connect() as cursor:
            cursor.execute("DELETE FROM runs WHERE id = %s AND owner_id = %s", (run_id, owner_id))
            return cursor.rowcount > 0

    def salvar_perfil(self, nome, termos):
        with self.connect() as cursor:
            cursor.execute(
                "INSERT INTO perfis_busca (nome, termos_json, created_at) VALUES (%s, %s, now()) RETURNING id",
                (nome, json.dumps(termos, ensure_ascii=False)),
            )
            return cursor.fetchone()["id"]

    def salvar_perfil_do_owner(self, owner_id, nome, termos):
        with self.connect() as cursor:
            cursor.execute(
                "INSERT INTO perfis_busca (owner_id, nome, termos_json, created_at) VALUES (%s, %s, %s, now()) RETURNING id",
                (owner_id, nome, json.dumps(termos, ensure_ascii=False)),
            )
            return cursor.fetchone()["id"]

    def listar_perfis(self):
        with self.connect() as cursor:
            cursor.execute("SELECT id, nome, termos_json, created_at FROM perfis_busca ORDER BY lower(nome), id")
            return [_as_dict(row) for row in cursor.fetchall()]

    def listar_perfis_do_owner(self, owner_id):
        with self.connect() as cursor:
            cursor.execute(
                "SELECT id, nome, termos_json, created_at FROM perfis_busca WHERE owner_id = %s ORDER BY lower(nome), id",
                (owner_id,),
            )
            return [_as_dict(row) for row in cursor.fetchall()]

    def excluir_perfil(self, perfil_id):
        with self.connect() as cursor:
            cursor.execute("DELETE FROM perfis_busca WHERE id = %s", (perfil_id,))
            return cursor.rowcount > 0

    def excluir_perfil_do_owner(self, perfil_id, owner_id):
        with self.connect() as cursor:
            cursor.execute("DELETE FROM perfis_busca WHERE id = %s AND owner_id = %s", (perfil_id, owner_id))
            return cursor.rowcount > 0

    def salvar_contrato(self, contrato, participantes):
        contract = _contrato_defaults(contrato)
        with self.connect() as cursor:
            cursor.execute(
                """INSERT INTO contratos (run_id, numero_controle, orgao_cnpj, orgao_nome, uf, municipio,
                   ano, sequencial, objeto, valor, data_publicacao, status, motivo_status, item_url)
                   VALUES (%(run_id)s, %(numero_controle)s, %(orgao_cnpj)s, %(orgao_nome)s, %(uf)s, %(municipio)s,
                   %(ano)s, %(sequencial)s, %(objeto)s, %(valor)s, %(data_publicacao)s, %(status)s, %(motivo_status)s, %(item_url)s)
                   ON CONFLICT (run_id, numero_controle) DO UPDATE SET orgao_nome = EXCLUDED.orgao_nome,
                     objeto = EXCLUDED.objeto, valor = EXCLUDED.valor, status = EXCLUDED.status,
                     motivo_status = EXCLUDED.motivo_status, item_url = EXCLUDED.item_url RETURNING id""",
                contract,
            )
            contract_id = cursor.fetchone()["id"]
            for participant in participantes:
                cursor.execute(
                    """INSERT INTO participantes (contrato_id, cnpj, nome, papel, valor_homologado, situacao_cadastral)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (contrato_id, cnpj) DO UPDATE SET nome = EXCLUDED.nome, papel = EXCLUDED.papel,
                       valor_homologado = EXCLUDED.valor_homologado, situacao_cadastral = EXCLUDED.situacao_cadastral""",
                    (
                        contract_id,
                        participant["cnpj"],
                        participant.get("nome", ""),
                        participant["papel"],
                        participant.get("valor_homologado"),
                        participant.get("situacao_cadastral", ""),
                    ),
                )
            return contract_id

    def salvar_cnpjs_auditoria(self, contrato_id, run_id, registros):
        with self.connect() as cursor:
            for registro in registros:
                cursor.execute(
                    """INSERT INTO cnpjs_auditoria (run_id, contrato_id, cnpj, nome, source, disposition, reason, origin_file, situacao_cadastral)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (run_id, contrato_id, cnpj, disposition) DO UPDATE SET nome = EXCLUDED.nome,
                         source = EXCLUDED.source, reason = EXCLUDED.reason, origin_file = EXCLUDED.origin_file,
                         situacao_cadastral = EXCLUDED.situacao_cadastral""",
                    (
                        run_id,
                        contrato_id,
                        registro["cnpj"],
                        registro.get("nome", ""),
                        registro["source"],
                        registro["disposition"],
                        registro.get("reason", ""),
                        registro.get("origin_file", ""),
                        registro.get("situacao_cadastral", ""),
                    ),
                )

    def salvar_evidencias_cnpj(self, contrato_id, run_id, evidencias):
        with self.connect() as cursor:
            cursor.execute("DELETE FROM cnpj_evidencias WHERE contrato_id = %s", (contrato_id,))
            for evidencia in evidencias:
                cursor.execute(
                    """INSERT INTO cnpj_evidencias (run_id, contrato_id, cnpj, origin_file, scan_pass, page_number, category, signal, excerpt)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        run_id,
                        contrato_id,
                        evidencia["cnpj"],
                        evidencia["origin_file"],
                        evidencia["scan_pass"],
                        evidencia.get("page_number", 0),
                        evidencia["category"],
                        evidencia.get("signal", ""),
                        evidencia.get("excerpt", ""),
                    ),
                )

    def listar_evidencias_cnpj(self, run_id, contrato_id=None):
        query, params = "SELECT * FROM cnpj_evidencias WHERE run_id = %s", [run_id]
        if contrato_id is not None:
            query += " AND contrato_id = %s"
            params.append(contrato_id)
        query += " ORDER BY contrato_id, cnpj, page_number, id"
        with self.connect() as cursor:
            cursor.execute(query, params)
            return [_as_dict(row) for row in cursor.fetchall()]

    def salvar_metricas_funil(self, contrato_id, run_id, metricas):
        values = {**_metricas_defaults(), **metricas, "contrato_id": contrato_id, "run_id": run_id}
        columns = ", ".join(values)
        placeholders = ", ".join(f"%({key})s" for key in values)
        updates = ", ".join(f"{key} = EXCLUDED.{key}" for key in _metricas_defaults())
        with self.connect() as cursor:
            cursor.execute(
                f"INSERT INTO metricas_funil ({columns}) VALUES ({placeholders}) ON CONFLICT (contrato_id) DO UPDATE SET {updates}",
                values,
            )

    def listar_cnpjs_auditoria(self, run_id, disposition=None):
        query, params = "SELECT * FROM cnpjs_auditoria WHERE run_id = %s", [run_id]
        if disposition:
            query += " AND disposition = %s"
            params.append(disposition)
        with self.connect() as cursor:
            cursor.execute(query + " ORDER BY disposition, cnpj", params)
            return [_as_dict(row) for row in cursor.fetchall()]

    def somar_metricas_run(self, run_id):
        aliases = ", ".join(f"COALESCE(SUM({name}), 0) AS {name}" for name in _metricas_defaults())
        with self.connect() as cursor:
            cursor.execute(f"SELECT {aliases} FROM metricas_funil WHERE run_id = %s", (run_id,))
            return _as_dict(cursor.fetchone())

    def contar_contratos_status(self, run_id):
        with self.connect() as cursor:
            cursor.execute(
                """SELECT status, motivo_status, COUNT(*) AS total FROM contratos WHERE run_id = %s
                   GROUP BY status, motivo_status ORDER BY status, motivo_status""",
                (run_id,),
            )
            return [_as_dict(row) for row in cursor.fetchall()]

    def listar_contratos(self, uf=None, run_id=None, incluir_ocultos=True):
        filters, params = [], []
        if run_id:
            filters.append("run_id = %s")
            params.append(run_id)
        if uf:
            filters.append("uf = %s")
            params.append(uf)
        if not incluir_ocultos:
            filters.append("status = 'final'")
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self.connect() as cursor:
            cursor.execute("SELECT * FROM contratos" + where + " ORDER BY data_publicacao DESC", params)
            contracts = [_as_dict(row) for row in cursor.fetchall()]
            for contract in contracts:
                cursor.execute(
                    "SELECT cnpj, nome, papel, valor_homologado, situacao_cadastral FROM participantes WHERE contrato_id = %s ORDER BY papel, nome",
                    (contract["id"],),
                )
                contract["participantes"] = [_as_dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT cnpj, nome, source, disposition, reason, origin_file, situacao_cadastral FROM cnpjs_auditoria WHERE contrato_id = %s ORDER BY disposition, cnpj",
                    (contract["id"],),
                )
                contract["auditoria"] = [_as_dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT cnpj, origin_file, scan_pass, page_number, category, signal, excerpt FROM cnpj_evidencias WHERE contrato_id = %s ORDER BY cnpj, page_number, id",
                    (contract["id"],),
                )
                contract["evidencias"] = [_as_dict(row) for row in cursor.fetchall()]
            return contracts

    def salvar_documento(
        self,
        run_id,
        source_url,
        *,
        contrato_id=None,
        s3_bucket=None,
        s3_key=None,
        sha256=None,
        size_bytes=None,
        content_type=None,
        status="downloaded",
    ):
        with self.connect() as cursor:
            cursor.execute(
                """INSERT INTO documentos (run_id, contrato_id, source_url, s3_bucket, s3_key, sha256, size_bytes, content_type, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (run_id, source_url) DO UPDATE SET contrato_id = EXCLUDED.contrato_id,
                   s3_bucket = EXCLUDED.s3_bucket, s3_key = EXCLUDED.s3_key, sha256 = EXCLUDED.sha256,
                   size_bytes = EXCLUDED.size_bytes, content_type = EXCLUDED.content_type, status = EXCLUDED.status
                   RETURNING id""",
                (run_id, contrato_id, source_url, s3_bucket, s3_key, sha256, size_bytes, content_type, status),
            )
            return cursor.fetchone()["id"]

    def listar_documentos(self, run_id):
        with self.connect() as cursor:
            cursor.execute("SELECT * FROM documentos WHERE run_id = %s ORDER BY id", (run_id,))
            return [_as_dict(row) for row in cursor.fetchall()]


def _contrato_defaults(contrato):
    return {"run_id": None, "status": "final", "motivo_status": "", "item_url": "", **contrato}


def _metricas_defaults():
    return {
        "atas_lidas": 0,
        "atas_falhas": 0,
        "cnpjs_ata_unicos": 0,
        "removido_invalido": 0,
        "removido_orgao": 0,
        "removido_vencedor": 0,
        "candidatos_inconclusivos": 0,
        "perdedores_final": 0,
        "vencedores": 0,
        "vencedores_inferidos": 0,
        "resultado_final": 0,
        "documentos_listados": 0,
        "documentos_prioritarios_lidos": 0,
        "documentos_fallback_lidos": 0,
        "documentos_ignorados": 0,
        "documentos_duplicados": 0,
    }


def _as_dict(row):
    """Keep the public API JSON/template-safe while DB timestamps stay TIMESTAMPTZ."""
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in dict(row).items()}
