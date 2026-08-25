"""Add browser-scoped anonymous ownership without changing worker semantics."""

from alembic import op

revision = "20260820_02"
down_revision = "20260820_01"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS anonymous_identities (
      id UUID PRIMARY KEY,
      token_hash CHAR(64) NOT NULL UNIQUE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      expires_at TIMESTAMPTZ NOT NULL
    );
    ALTER TABLE runs ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES anonymous_identities(id);
    ALTER TABLE perfis_busca ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES anonymous_identities(id);
    """)
    # The historical global constraint prevents equal names owned by different browsers.
    op.execute("ALTER TABLE perfis_busca DROP CONSTRAINT IF EXISTS perfis_busca_nome_key")
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_runs_owner_created ON runs(owner_id, created_at DESC, id DESC);
    CREATE INDEX IF NOT EXISTS idx_perfis_busca_owner ON perfis_busca(owner_id, id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_perfis_busca_owner_nome
      ON perfis_busca(owner_id, lower(nome)) WHERE owner_id IS NOT NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS uq_runs_one_active_per_owner
      ON runs(owner_id) WHERE owner_id IS NOT NULL AND status IN ('queued', 'running');
    """)


def downgrade():
    # Ownership data is deliberately retained on rollback.
    pass
