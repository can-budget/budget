from alembic import context
from sqlalchemy import create_engine, pool

from can_api.config import settings

if context.is_offline_mode():
    context.configure(
        url=settings().database_url, literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    context.configure(connection=context.config.attributes["connection"])
    with context.begin_transaction():
        context.run_migrations()
else:
    connectable = create_engine(settings().database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
