"""Persistencia leve em SQLite para o resultado da analise de atas.

Sem servidor: um unico arquivo .db, adequado para rodar em um container.
Modela contratos e seus participantes (adjudicatario + demais participantes).
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
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
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self):
        with self.connect() as conn:
            conn.executescript(SCHEMA)

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
                        "WHERE contrato_id = ? ORDER BY papel DESC",
                        (contrato["id"],),
                    ).fetchall()
                ]
            return contratos
