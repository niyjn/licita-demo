"""Persistencia leve em SQLite para o resultado da analise de atas.

Sem servidor: um unico arquivo .db, adequado para rodar em um container.
Modela contratos e seus participantes (adjudicatario + demais participantes).
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 2

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
    finished_at TEXT
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
    UNIQUE (run_id, numero_controle)
);

CREATE TABLE IF NOT EXISTS participantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contrato_id INTEGER NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
    cnpj TEXT NOT NULL,
    nome TEXT,
    papel TEXT NOT NULL,            -- 'adjudicatario' ou 'participante'
    valor_homologado REAL,
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
    UNIQUE (run_id, contrato_id, cnpj, disposition)
);

CREATE TABLE IF NOT EXISTS metricas_funil (
    contrato_id INTEGER PRIMARY KEY REFERENCES contratos(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    atas_lidas INTEGER NOT NULL DEFAULT 0,
    cnpjs_ata_unicos INTEGER NOT NULL DEFAULT 0,
    removido_invalido INTEGER NOT NULL DEFAULT 0,
    removido_orgao INTEGER NOT NULL DEFAULT 0,
    removido_vencedor INTEGER NOT NULL DEFAULT 0,
    perdedores_final INTEGER NOT NULL DEFAULT 0,
    vencedores INTEGER NOT NULL DEFAULT 0,
    resultado_final INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_contratos_uf ON contratos(uf);
CREATE INDEX IF NOT EXISTS idx_contratos_run ON contratos(run_id);
CREATE INDEX IF NOT EXISTS idx_auditoria_run_disposition ON cnpjs_auditoria(run_id, disposition);
"""


class Storage:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self):
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                self._recriar_schema(conn)
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _recriar_schema(self, conn):
        conn.executescript(
            """
            DROP TABLE IF EXISTS metricas_funil;
            DROP TABLE IF EXISTS cnpjs_auditoria;
            DROP TABLE IF EXISTS participantes;
            DROP TABLE IF EXISTS contratos;
            DROP TABLE IF EXISTS runs;
            """
        )

    def criar_run(self, run_id, params_json="{}"):
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, status, progress, message, params_json, created_at)
                VALUES (?, 'queued', 0, 'Analise na fila.', ?, ?)
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

    def obter_run(self, run_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def salvar_contrato(self, contrato, participantes):
        """contrato: dict com chaves do schema; participantes: lista de dicts."""
        with self.connect() as conn:
            contrato_normalizado = _contrato_defaults(contrato)
            cur = conn.execute(
                """
                INSERT INTO contratos
                    (run_id, numero_controle, orgao_cnpj, orgao_nome, uf, municipio,
                     ano, sequencial, objeto, valor, data_publicacao, status, motivo_status)
                VALUES (:run_id, :numero_controle, :orgao_cnpj, :orgao_nome, :uf, :municipio,
                        :ano, :sequencial, :objeto, :valor, :data_publicacao, :status, :motivo_status)
                ON CONFLICT(run_id, numero_controle) DO UPDATE SET
                    run_id=excluded.run_id, orgao_nome=excluded.orgao_nome,
                    objeto=excluded.objeto, valor=excluded.valor,
                    status=excluded.status, motivo_status=excluded.motivo_status
                """,
                contrato_normalizado,
            )
            contrato_id = cur.lastrowid or self._id_por_controle(
                conn, contrato_normalizado["numero_controle"], contrato_normalizado["run_id"]
            )
            for p in participantes:
                conn.execute(
                    """
                    INSERT INTO participantes (contrato_id, cnpj, nome, papel, valor_homologado)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(contrato_id, cnpj) DO UPDATE SET
                        nome=excluded.nome, papel=excluded.papel,
                        valor_homologado=excluded.valor_homologado
                    """,
                    (contrato_id, p["cnpj"], p.get("nome", ""), p["papel"], p.get("valor_homologado")),
                )
            return contrato_id

    def salvar_cnpjs_auditoria(self, contrato_id, run_id, registros):
        with self.connect() as conn:
            for registro in registros:
                conn.execute(
                    """
                    INSERT INTO cnpjs_auditoria
                        (run_id, contrato_id, cnpj, nome, source, disposition, reason, origin_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, contrato_id, cnpj, disposition) DO UPDATE SET
                        nome=excluded.nome, source=excluded.source,
                        reason=excluded.reason, origin_file=excluded.origin_file
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
                    ),
                )

    def salvar_metricas_funil(self, contrato_id, run_id, metricas):
        valores = {
            "atas_lidas": 0,
            "cnpjs_ata_unicos": 0,
            "removido_invalido": 0,
            "removido_orgao": 0,
            "removido_vencedor": 0,
            "perdedores_final": 0,
            "vencedores": 0,
            "resultado_final": 0,
            **metricas,
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO metricas_funil
                    (contrato_id, run_id, atas_lidas, cnpjs_ata_unicos,
                     removido_invalido, removido_orgao, removido_vencedor,
                     perdedores_final, vencedores, resultado_final)
                VALUES (:contrato_id, :run_id, :atas_lidas, :cnpjs_ata_unicos,
                        :removido_invalido, :removido_orgao, :removido_vencedor,
                        :perdedores_final, :vencedores, :resultado_final)
                ON CONFLICT(contrato_id) DO UPDATE SET
                    atas_lidas=excluded.atas_lidas,
                    cnpjs_ata_unicos=excluded.cnpjs_ata_unicos,
                    removido_invalido=excluded.removido_invalido,
                    removido_orgao=excluded.removido_orgao,
                    removido_vencedor=excluded.removido_vencedor,
                    perdedores_final=excluded.perdedores_final,
                    vencedores=excluded.vencedores,
                    resultado_final=excluded.resultado_final
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
                    COALESCE(SUM(cnpjs_ata_unicos), 0) AS cnpjs_ata_unicos,
                    COALESCE(SUM(removido_invalido), 0) AS removido_invalido,
                    COALESCE(SUM(removido_orgao), 0) AS removido_orgao,
                    COALESCE(SUM(removido_vencedor), 0) AS removido_vencedor,
                    COALESCE(SUM(perdedores_final), 0) AS perdedores_final,
                    COALESCE(SUM(vencedores), 0) AS vencedores,
                    COALESCE(SUM(resultado_final), 0) AS resultado_final
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

    def listar_contratos(self, uf=None):
        query = "SELECT * FROM contratos"
        params = []
        if uf:
            query += " WHERE uf = ?"
            params.append(uf)
        query += " ORDER BY data_publicacao DESC"
        with self.connect() as conn:
            contratos = [dict(r) for r in conn.execute(query, params).fetchall()]
            for contrato in contratos:
                contrato["participantes"] = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT cnpj, nome, papel, valor_homologado FROM participantes "
                        "WHERE contrato_id = ? ORDER BY papel ASC, nome ASC",
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
        **contrato,
    }
