"""Persistencia leve em SQLite para o resultado da analise de atas.

Sem servidor: um unico arquivo .db, adequado para rodar em um container.
Modela contratos e seus participantes (adjudicatario + demais participantes).
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

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
    numero_controle TEXT UNIQUE,
    orgao_cnpj TEXT,
    orgao_nome TEXT,
    uf TEXT,
    municipio TEXT,
    ano TEXT,
    sequencial TEXT,
    objeto TEXT,
    valor TEXT,
    data_publicacao TEXT
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

CREATE INDEX IF NOT EXISTS idx_contratos_uf ON contratos(uf);
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
            conn.executescript(SCHEMA)

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
            cur = conn.execute(
                """
                INSERT INTO contratos
                    (numero_controle, orgao_cnpj, orgao_nome, uf, municipio,
                     ano, sequencial, objeto, valor, data_publicacao)
                VALUES (:numero_controle, :orgao_cnpj, :orgao_nome, :uf, :municipio,
                        :ano, :sequencial, :objeto, :valor, :data_publicacao)
                ON CONFLICT(numero_controle) DO UPDATE SET
                    orgao_nome=excluded.orgao_nome, objeto=excluded.objeto, valor=excluded.valor
                """,
                contrato,
            )
            contrato_id = cur.lastrowid or self._id_por_controle(conn, contrato["numero_controle"])
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

    def _id_por_controle(self, conn, numero_controle):
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
