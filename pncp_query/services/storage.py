"""Persistência leve em SQLite/PostgreSQL para o resultado da análise de atas.

Sem servidor: um unico arquivo .db para local, ou PostgreSQL na nuvem AWS.
Modela contratos e seus participantes (adjudicatario + demais participantes).
"""

import json
import sqlite3
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 8

_INITIALIZED_DBS: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    worker_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    heartbeat_at TEXT,
    duration_seconds INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS contratos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    numero_controle TEXT,
    orgao_cnpj TEXT,
    orgao_nome TEXT,
    uf TEXT,
    municipio TEXT,
    ano TEXT,
    sequencial TEXT,
    objeto TEXT,
    valor TEXT,
    data_publicacao TEXT,
    status TEXT NOT NULL DEFAULT 'final',
    motivo_status TEXT NOT NULL DEFAULT '',
    item_url TEXT NOT NULL DEFAULT '',
    UNIQUE (run_id, numero_controle)
);

CREATE TABLE IF NOT EXISTS participantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contrato_id INTEGER NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
    cnpj TEXT NOT NULL,
    nome TEXT,
    papel TEXT NOT NULL,            -- 'adjudicatario' ou 'participante'
    valor_homologado REAL,
    situacao_cadastral TEXT NOT NULL DEFAULT '',
    UNIQUE (contrato_id, cnpj)
);

CREATE TABLE IF NOT EXISTS cnpjs_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    contrato_id INTEGER NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
    cnpj TEXT NOT NULL,
    nome TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    disposition TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    origin_file TEXT NOT NULL DEFAULT '',
    situacao_cadastral TEXT NOT NULL DEFAULT '',
    UNIQUE (run_id, contrato_id, cnpj, disposition)
);

CREATE TABLE IF NOT EXISTS cnpj_evidencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    contrato_id INTEGER NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
    cnpj TEXT NOT NULL,
    origin_file TEXT NOT NULL,
    scan_pass TEXT NOT NULL,
    page_number INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL,
    signal TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS metricas_funil (
    contrato_id INTEGER PRIMARY KEY REFERENCES contratos(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    atas_lidas INTEGER NOT NULL DEFAULT 0,
    atas_falhas INTEGER NOT NULL DEFAULT 0,
    cnpjs_ata_unicos INTEGER NOT NULL DEFAULT 0,
    removido_invalido INTEGER NOT NULL DEFAULT 0,
    removido_orgao INTEGER NOT NULL DEFAULT 0,
    removido_vencedor INTEGER NOT NULL DEFAULT 0,
    candidatos_inconclusivos INTEGER NOT NULL DEFAULT 0,
    perdedores_final INTEGER NOT NULL DEFAULT 0,
    vencedores INTEGER NOT NULL DEFAULT 0,
    vencedores_inferidos INTEGER NOT NULL DEFAULT 0,
    resultado_final INTEGER NOT NULL DEFAULT 0,
    documentos_listados INTEGER NOT NULL DEFAULT 0,
    documentos_prioritarios_lidos INTEGER NOT NULL DEFAULT 0,
    documentos_fallback_lidos INTEGER NOT NULL DEFAULT 0,
    documentos_ignorados INTEGER NOT NULL DEFAULT 0,
    documentos_duplicados INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS perfis_busca (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE,
    termos_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contratos_uf ON contratos(uf);
CREATE INDEX IF NOT EXISTS idx_contratos_run ON contratos(run_id);
CREATE INDEX IF NOT EXISTS idx_auditoria_run_disposition ON cnpjs_auditoria(run_id, disposition);
CREATE INDEX IF NOT EXISTS idx_evidencias_run_cnpj ON cnpj_evidencias(run_id, cnpj);
CREATE INDEX IF NOT EXISTS idx_runs_status_created ON runs(status, created_at);
"""


class RowWrapper(dict):
    def __init__(self, d):
        super().__init__(d)
        self._keys = list(d.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._keys[key])
        return super().__getitem__(key)


class DatabaseCursorWrapper:
    def __init__(self, cursor, is_postgres):
        self.cursor = cursor
        self.is_postgres = is_postgres
        self._lastrowid = None

    @property
    def rowcount(self):
        return self.cursor.rowcount

    @property
    def lastrowid(self):
        if self.is_postgres:
            return self._lastrowid
        return self.cursor.lastrowid

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        if self.is_postgres:
            return RowWrapper(row)
        return row

    def fetchall(self):
        rows = self.cursor.fetchall()
        if self.is_postgres:
            return [RowWrapper(r) for r in rows]
        return rows

    def execute(self, sql, params=None):
        sql_pg = sql
        appended_returning = False
        
        if self.is_postgres:
            if params is not None and not isinstance(params, dict):
                sql_pg = sql_pg.replace('?', '%s')
            elif isinstance(params, dict):
                sql_pg = re.sub(r':([a-zA-Z_][a-zA-Z0-9_]*)', r'%(\1)s', sql_pg)

            if re.match(r'(?i)^\s*INSERT\s+INTO', sql_pg) and 'returning' not in sql_pg.lower():
                sql_pg = sql_pg.strip().rstrip(';')
                sql_pg += ' RETURNING id'
                appended_returning = True

        if params is None:
            self.cursor.execute(sql_pg)
        else:
            self.cursor.execute(sql_pg, params)

        if self.is_postgres and appended_returning:
            try:
                row = self.cursor.fetchone()
                if row:
                    if isinstance(row, dict):
                        self._lastrowid = row.get('id') or list(row.values())[0]
                    else:
                        self._lastrowid = row[0]
            except Exception:
                pass
        
        return self


class DatabaseConnectionWrapper:
    def __init__(self, conn, is_postgres):
        self.conn = conn
        self.is_postgres = is_postgres

    def execute(self, sql, params=None):
        cur = self.conn.cursor()
        wrapper = DatabaseCursorWrapper(cur, self.is_postgres)
        wrapper.execute(sql, params)
        return wrapper

    def executescript(self, sql_script):
        if self.is_postgres:
            sql_clean = re.sub(r'(?i)PRAGMA\s+[^;]+;', '', sql_script)
            sql_clean = sql_clean.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur = self.conn.cursor()
            cur.execute(sql_clean)
            return DatabaseCursorWrapper(cur, self.is_postgres)
        else:
            return self.conn.executescript(sql_script)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


class Storage:
    def __init__(self, db_path):
        db_str = str(db_path)
        self.is_postgres = db_str.startswith("postgresql://") or db_str.startswith("postgres://")
        if self.is_postgres:
            self.db_url = db_str
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            
        key = db_str
        if key not in _INITIALIZED_DBS:
            self.init_db()
            _INITIALIZED_DBS.add(key)

    @contextmanager
    def connect(self):
        if self.is_postgres:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)
            wrapped_conn = DatabaseConnectionWrapper(conn, True)
            try:
                yield wrapped_conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys = ON")
            wrapped_conn = DatabaseConnectionWrapper(conn, False)
            try:
                yield wrapped_conn
                conn.commit()
            finally:
                conn.close()

    def init_db(self):
        with self.connect() as conn:
            if self.is_postgres:
                conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
                cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
                row = cur.fetchone()
                version = row["version"] if row else 0
            else:
                version = conn.execute("PRAGMA user_version").fetchone()[0]

            if version < SCHEMA_VERSION:
                self._run_migrations(conn, version)
            
            conn.executescript(SCHEMA)
            
            if self.is_postgres:
                conn.execute("DELETE FROM schema_version")
                conn.execute("INSERT INTO schema_version (version) VALUES (%s)", (SCHEMA_VERSION,))
            else:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


    def _run_migrations(self, conn, current_version):
        if current_version == 0:
            return  # Fresh DB, script handles it
        
        # Example incremental upgrades
        if current_version < 3:
            # Safely add situacao_cadastral columns if they don't exist
            try:
                conn.execute("ALTER TABLE participantes ADD COLUMN situacao_cadastral TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        if current_version < 5:
            for coluna in (
                "candidatos_inconclusivos INTEGER NOT NULL DEFAULT 0",
                "documentos_listados INTEGER NOT NULL DEFAULT 0",
                "documentos_prioritarios_lidos INTEGER NOT NULL DEFAULT 0",
                "documentos_fallback_lidos INTEGER NOT NULL DEFAULT 0",
                "documentos_ignorados INTEGER NOT NULL DEFAULT 0",
                "documentos_duplicados INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    conn.execute(f"ALTER TABLE metricas_funil ADD COLUMN {coluna}")
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute("ALTER TABLE cnpjs_auditoria ADD COLUMN situacao_cadastral TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        if current_version < 6:
            try:
                conn.execute("ALTER TABLE metricas_funil ADD COLUMN vencedores_inferidos INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
        if current_version < 7:
            for coluna in (
                "worker_id TEXT",
                "attempt_count INTEGER NOT NULL DEFAULT 0",
                "heartbeat_at TEXT",
            ):
                try:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {coluna}")
                except sqlite3.OperationalError:
                    pass
        if current_version < 8:
            try:
                conn.execute("ALTER TABLE runs ADD COLUMN duration_seconds INTEGER NOT NULL DEFAULT 0")
            except (sqlite3.OperationalError, Exception):
                pass

    def criar_run(self, run_id, params_json="{}"):
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, status, progress, message, params_json, created_at)
                VALUES (?, 'queued', 0, 'Análise na fila.', ?, ?)
                """,
                (run_id, params_json, now),
            )
        return run_id

    def criar_run_se_disponivel(self, run_id, params_json="{}"):
        """Cria a run somente se não houver outra queued/running (apenas para SQLite)."""
        now = _now()
        with self.connect() as conn:
            if self.is_postgres:
                conn.execute(
                    """
                    INSERT INTO runs (id, status, progress, message, params_json, created_at)
                    VALUES (?, 'queued', 0, 'Análise na fila.', ?, ?)
                    """,
                    (run_id, params_json, now),
                )
                return run_id
            
            conn.execute("BEGIN IMMEDIATE")
            ativa = conn.execute(
                "SELECT id FROM runs WHERE status IN ('queued', 'running') LIMIT 1"
            ).fetchone()
            if ativa:
                return None
            conn.execute(
                """
                INSERT INTO runs (id, status, progress, message, params_json, created_at)
                VALUES (?, 'queued', 0, 'Análise na fila.', ?, ?)
                """,
                (run_id, params_json, now),
            )
        return run_id

    def atualizar_run(self, run_id, status=None, progress=None, message=None, error=None):
        updates = []
        params = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status == "running":
                updates.append("started_at = COALESCE(started_at, ?)")
                params.append(_now())
            if status in {"done", "error"}:
                updates.append("finished_at = ?")
                params.append(_now())
        if progress is not None:
            updates.append("progress = ?")
            params.append(float(progress))
        if message is not None:
            updates.append("message = ?")
            params.append(str(message))
        if error is not None:
            updates.append("error = ?")
            params.append(str(error))
        if not updates:
            return

        params.append(run_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(updates)} WHERE id = ?", params)

    def claim_next_run(self, worker_id):
        """Reivindica, em uma única transação, a run queued mais antiga."""
        now = _now()
        with self.connect() as conn:
            if self.is_postgres:
                cur = conn.execute(
                    """
                    SELECT id FROM runs 
                    WHERE status = 'queued' 
                    ORDER BY created_at ASC, id ASC 
                    LIMIT 1 
                    FOR UPDATE SKIP LOCKED
                    """
                )
                run = cur.fetchone()
                if not run:
                    return None
                
                conn.execute(
                    """
                    UPDATE runs
                    SET status = 'running', progress = 0, message = 'Análise iniciada.',
                        started_at = COALESCE(started_at, ?), heartbeat_at = ?, worker_id = ?,
                        attempt_count = attempt_count + 1
                    WHERE id = ?
                    """,
                    (now, now, worker_id, run["id"]),
                )
                return dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run["id"],)).fetchone())
            else:
                conn.execute("BEGIN IMMEDIATE")
                run = conn.execute(
                    "SELECT * FROM runs WHERE status = 'queued' ORDER BY created_at ASC, id ASC LIMIT 1"
                ).fetchone()
                if not run:
                    return None
                cursor = conn.execute(
                    """
                    UPDATE runs
                    SET status = 'running', progress = 0, message = 'Análise iniciada.',
                        started_at = COALESCE(started_at, ?), heartbeat_at = ?, worker_id = ?,
                        attempt_count = attempt_count + 1
                    WHERE id = ? AND status = 'queued'
                    """,
                    (now, now, worker_id, run["id"]),
                )
                if cursor.rowcount != 1:
                    return None
                return dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run["id"],)).fetchone())

    def heartbeat_run(self, run_id, worker_id, progress=None, message=None, duration_seconds=None):
        updates = ["heartbeat_at = ?"]
        params = [_now()]
        if progress is not None:
            updates.append("progress = ?")
            params.append(float(progress))
        if message is not None:
            updates.append("message = ?")
            params.append(str(message))
        if duration_seconds is not None:
            updates.append("duration_seconds = ?")
            params.append(int(duration_seconds))
        params.extend((run_id, worker_id))
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE runs SET {', '.join(updates)} WHERE id = ? AND status = 'running' AND worker_id = ?",
                params,
            )
            return cursor.rowcount == 1

    def complete_claimed_run(self, run_id, worker_id, message="Análise concluída.", duration_seconds=None):
        now = _now()
        updates = ["status = 'done'", "progress = 100", "message = ?", "error = ''", "heartbeat_at = ?", "finished_at = ?"]
        params = [message, now, now]
        if duration_seconds is not None:
            updates.append("duration_seconds = ?")
            params.append(int(duration_seconds))
        params.extend((run_id, worker_id))
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE runs SET {', '.join(updates)} WHERE id = ? AND status = 'running' AND worker_id = ?",
                params,
            )
            return cursor.rowcount == 1

    def fail_claimed_run(self, run_id, worker_id, error, message="Análise falhou.", duration_seconds=None):
        now = _now()
        updates = ["status = 'error'", "progress = 100", "message = ?", "error = ?", "heartbeat_at = ?", "finished_at = ?"]
        params = [message, str(error), now, now]
        if duration_seconds is not None:
            updates.append("duration_seconds = ?")
            params.append(int(duration_seconds))
        params.extend((run_id, worker_id))
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE runs SET {', '.join(updates)} WHERE id = ? AND status = 'running' AND worker_id = ?",
                params,
            )
            return cursor.rowcount == 1

    def limpar_runs_travadas(self, timeout_segundos=3600):
        """Marca runs travadas (ex: erro silencioso) há mais de timeout_segundos como 'error'."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, started_at, heartbeat_at, created_at FROM runs WHERE status = 'running'"
            ).fetchall()
            now = datetime.now()
            for row in rows:
                ref_time_str = row["heartbeat_at"] or row["started_at"] or row["created_at"]
                if not ref_time_str:
                    continue
                try:
                    ref_time = datetime.fromisoformat(ref_time_str)
                    if (now - ref_time).total_seconds() > timeout_segundos:
                        conn.execute(
                            "UPDATE runs SET status = 'error', message = 'Análise excedeu o tempo limite.', "
                            "error = 'Timeout de processamento.', finished_at = ? WHERE id = ? AND status = 'running'",
                            (now.isoformat(timespec="seconds"), row["id"])
                        )
                except Exception:
                    pass

    def obter_run(self, run_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def ultima_run(self):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def listar_runs(self, limit=20, offset=0, status=None):
        filtros = []
        params = []
        if status:
            filtros.append("r.status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
        params.extend((max(1, int(limit)), max(0, int(offset))))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r.*, COUNT(c.id) AS contratos_count
                FROM runs r
                LEFT JOIN contratos c ON c.run_id = r.id
                {where}
                GROUP BY r.id
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def contar_runs(self, status=None):
        query = "SELECT COUNT(*) FROM runs"
        params = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        with self.connect() as conn:
            return int(conn.execute(query, params).fetchone()[0])

    def excluir_run(self, run_id):
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            return cursor.rowcount > 0

    def salvar_perfil(self, nome, termos):
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO perfis_busca (nome, termos_json, created_at) VALUES (?, ?, ?)",
                (nome, json.dumps(termos, ensure_ascii=False), _now()),
            )
            return cursor.lastrowid

    def listar_perfis(self):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, nome, termos_json, created_at FROM perfis_busca ORDER BY nome COLLATE NOCASE, id"
            ).fetchall()
            return [dict(row) for row in rows]

    def excluir_perfil(self, perfil_id):
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM perfis_busca WHERE id = ?", (perfil_id,))
            return cursor.rowcount > 0

    def salvar_contrato(self, contrato, participantes):
        """contrato: dict com chaves do schema; participantes: lista de dicts."""
        with self.connect() as conn:
            contrato_normalizado = _contrato_defaults(contrato)
            cur = conn.execute(
                """
                INSERT INTO contratos
                    (run_id, numero_controle, orgao_cnpj, orgao_nome, uf, municipio,
                     ano, sequencial, objeto, valor, data_publicacao, status, motivo_status, item_url)
                VALUES (:run_id, :numero_controle, :orgao_cnpj, :orgao_nome, :uf, :municipio,
                        :ano, :sequencial, :objeto, :valor, :data_publicacao, :status, :motivo_status, :item_url)
                ON CONFLICT(run_id, numero_controle) DO UPDATE SET
                    run_id=excluded.run_id, orgao_nome=excluded.orgao_nome,
                    objeto=excluded.objeto, valor=excluded.valor,
                    status=excluded.status, motivo_status=excluded.motivo_status,
                    item_url=excluded.item_url
                """,
                contrato_normalizado,
            )
            contrato_id = cur.lastrowid or self._id_por_controle(
                conn, contrato_normalizado["numero_controle"], contrato_normalizado["run_id"]
            )
            for p in participantes:
                conn.execute(
                    """
                    INSERT INTO participantes (contrato_id, cnpj, nome, papel, valor_homologado, situacao_cadastral)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(contrato_id, cnpj) DO UPDATE SET
                        nome=excluded.nome, papel=excluded.papel,
                        valor_homologado=excluded.valor_homologado,
                        situacao_cadastral=excluded.situacao_cadastral
                    """,
                    (
                        contrato_id, p["cnpj"], p.get("nome", ""), p["papel"],
                        p.get("valor_homologado"), p.get("situacao_cadastral", ""),
                    ),
                )
            return contrato_id

    def salvar_cnpjs_auditoria(self, contrato_id, run_id, registros):
        with self.connect() as conn:
            for registro in registros:
                conn.execute(
                    """
                    INSERT INTO cnpjs_auditoria
                        (run_id, contrato_id, cnpj, nome, source, disposition, reason, origin_file, situacao_cadastral)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, contrato_id, cnpj, disposition) DO UPDATE SET
                        nome=excluded.nome, source=excluded.source,
                        reason=excluded.reason, origin_file=excluded.origin_file,
                        situacao_cadastral=excluded.situacao_cadastral
                    """,
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
        with self.connect() as conn:
            conn.execute("DELETE FROM cnpj_evidencias WHERE contrato_id = ?", (contrato_id,))
            for evidencia in evidencias:
                conn.execute(
                    """
                    INSERT INTO cnpj_evidencias
                        (run_id, contrato_id, cnpj, origin_file, scan_pass,
                         page_number, category, signal, excerpt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
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
        query = "SELECT * FROM cnpj_evidencias WHERE run_id = ?"
        params = [run_id]
        if contrato_id is not None:
            query += " AND contrato_id = ?"
            params.append(contrato_id)
        query += " ORDER BY contrato_id, cnpj, page_number, id"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def salvar_metricas_funil(self, contrato_id, run_id, metricas):
        valores = {
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
            **metricas,
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO metricas_funil
                    (contrato_id, run_id, atas_lidas, atas_falhas, cnpjs_ata_unicos,
                     removido_invalido, removido_orgao, removido_vencedor,
                     candidatos_inconclusivos, perdedores_final, vencedores, vencedores_inferidos, resultado_final,
                     documentos_listados, documentos_prioritarios_lidos,
                     documentos_fallback_lidos, documentos_ignorados, documentos_duplicados)
                VALUES (:contrato_id, :run_id, :atas_lidas, :atas_falhas, :cnpjs_ata_unicos,
                        :removido_invalido, :removido_orgao, :removido_vencedor,
                        :candidatos_inconclusivos, :perdedores_final, :vencedores,
                        :vencedores_inferidos, :resultado_final,
                        :documentos_listados, :documentos_prioritarios_lidos,
                        :documentos_fallback_lidos, :documentos_ignorados, :documentos_duplicados)
                ON CONFLICT(contrato_id) DO UPDATE SET
                    atas_lidas=excluded.atas_lidas,
                    atas_falhas=excluded.atas_falhas,
                    cnpjs_ata_unicos=excluded.cnpjs_ata_unicos,
                    removido_invalido=excluded.removido_invalido,
                    removido_orgao=excluded.removido_orgao,
                    removido_vencedor=excluded.removido_vencedor,
                    candidatos_inconclusivos=excluded.candidatos_inconclusivos,
                    perdedores_final=excluded.perdedores_final,
                    vencedores=excluded.vencedores,
                    vencedores_inferidos=excluded.vencedores_inferidos,
                    resultado_final=excluded.resultado_final,
                    documentos_listados=excluded.documentos_listados,
                    documentos_prioritarios_lidos=excluded.documentos_prioritarios_lidos,
                    documentos_fallback_lidos=excluded.documentos_fallback_lidos,
                    documentos_ignorados=excluded.documentos_ignorados,
                    documentos_duplicados=excluded.documentos_duplicados
                """,
                {"contrato_id": contrato_id, "run_id": run_id, **valores},
            )

    def listar_cnpjs_auditoria(self, run_id, disposition=None):
        query = "SELECT * FROM cnpjs_auditoria WHERE run_id = ?"
        params = [run_id]
        if disposition:
            query += " AND disposition = ?"
            params.append(disposition)
        query += " ORDER BY disposition, cnpj"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def somar_metricas_run(self, run_id):
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(atas_lidas), 0) AS atas_lidas,
                    COALESCE(SUM(atas_falhas), 0) AS atas_falhas,
                    COALESCE(SUM(cnpjs_ata_unicos), 0) AS cnpjs_ata_unicos,
                    COALESCE(SUM(removido_invalido), 0) AS removido_invalido,
                    COALESCE(SUM(removido_orgao), 0) AS removido_orgao,
                    COALESCE(SUM(removido_vencedor), 0) AS removido_vencedor,
                    COALESCE(SUM(candidatos_inconclusivos), 0) AS candidatos_inconclusivos,
                    COALESCE(SUM(perdedores_final), 0) AS perdedores_final,
                    COALESCE(SUM(vencedores), 0) AS vencedores,
                    COALESCE(SUM(vencedores_inferidos), 0) AS vencedores_inferidos,
                    COALESCE(SUM(resultado_final), 0) AS resultado_final,
                    COALESCE(SUM(documentos_listados), 0) AS documentos_listados,
                    COALESCE(SUM(documentos_prioritarios_lidos), 0) AS documentos_prioritarios_lidos,
                    COALESCE(SUM(documentos_fallback_lidos), 0) AS documentos_fallback_lidos,
                    COALESCE(SUM(documentos_ignorados), 0) AS documentos_ignorados,
                    COALESCE(SUM(documentos_duplicados), 0) AS documentos_duplicados
                FROM metricas_funil
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            return dict(row)

    def _id_por_controle(self, conn, numero_controle, run_id=None):
        if run_id is not None:
            row = conn.execute(
                "SELECT id FROM contratos WHERE numero_controle = ? AND run_id = ?",
                (numero_controle, run_id),
            ).fetchone()
            return row["id"] if row else None
        row = conn.execute(
            "SELECT id FROM contratos WHERE numero_controle = ?", (numero_controle,)
        ).fetchone()
        return row["id"] if row else None

    def contar_contratos_status(self, run_id):
        """Retorna [{status, motivo_status, total}] para uma run — usado no funil de cobertura."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT status, motivo_status, COUNT(*) AS total
                FROM contratos
                WHERE run_id = ?
                GROUP BY status, motivo_status
                ORDER BY status, motivo_status
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def listar_contratos(self, uf=None, run_id=None, incluir_ocultos=True):
        query = "SELECT * FROM contratos"
        params = []
        filtros = []
        if run_id:
            filtros.append("run_id = ?")
            params.append(run_id)
        if uf:
            filtros.append("uf = ?")
            params.append(uf)
        if not incluir_ocultos:
            filtros.append("status = 'final'")
        if filtros:
            query += " WHERE " + " AND ".join(filtros)
        query += " ORDER BY data_publicacao DESC"
        with self.connect() as conn:
            contratos = [dict(r) for r in conn.execute(query, params).fetchall()]
            for contrato in contratos:
                contrato["participantes"] = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT cnpj, nome, papel, valor_homologado, situacao_cadastral FROM participantes "
                        "WHERE contrato_id = ? ORDER BY papel ASC, nome ASC",
                        (contrato["id"],),
                    ).fetchall()
                ]
                contrato["auditoria"] = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT cnpj, nome, source, disposition, reason, origin_file, situacao_cadastral "
                        "FROM cnpjs_auditoria WHERE contrato_id = ? ORDER BY disposition ASC, cnpj ASC",
                        (contrato["id"],),
                    ).fetchall()
                ]
                contrato["evidencias"] = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT cnpj, origin_file, scan_pass, page_number, category, signal, excerpt "
                        "FROM cnpj_evidencias WHERE contrato_id = ? ORDER BY cnpj, page_number, id",
                        (contrato["id"],),
                    ).fetchall()
                ]
            return contratos


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _contrato_defaults(contrato):
    return {
        "run_id": None,
        "status": "final",
        "motivo_status": "",
        "item_url": "",
        **contrato,
    }
