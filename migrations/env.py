from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from pncp_query.config import require_database_url

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def run_migrations_offline():
    context.configure(url=require_database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # Passing the DSN directly avoids ConfigParser interpolation of encoded
    # password characters such as `%`.
    connectable = create_engine(require_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
