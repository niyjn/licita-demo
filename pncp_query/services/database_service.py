from contextlib import contextmanager
from pathlib import Path

from pncp_query.config import DATABASE_URL, MIGRATIONS_DIR


class DatabaseService:
    def __init__(self, database_url=DATABASE_URL):
        self.database_url = database_url

    @contextmanager
    def connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Instale psycopg[binary] para usar checkpoints com PostgreSQL.") from exc

        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            yield conn

    def migrate(self, migrations_dir: Path = MIGRATIONS_DIR):
        with self.connect() as conn:
            for caminho in sorted(migrations_dir.glob("*.sql")):
                with caminho.open("r", encoding="utf-8") as arquivo:
                    conn.execute(arquivo.read())
            conn.commit()

    def execute_batch(self, sql, params, batch_size=100):
        total = 0
        with self.connect() as conn:
            with conn.cursor() as cur:
                batch = []
                for item in params:
                    batch.append(item)
                    if len(batch) >= batch_size:
                        cur.executemany(sql, batch)
                        total += len(batch)
                        batch.clear()
                if batch:
                    cur.executemany(sql, batch)
                    total += len(batch)
            conn.commit()
        return total

    def check(self):
        with self.connect() as conn:
            row = conn.execute("SELECT 1 AS ok").fetchone()
            return row["ok"] == 1
