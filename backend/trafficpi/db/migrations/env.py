"""Alembic environment: migrations run on a connection handed in by ``upgrade_to_head``."""

from alembic import context

from trafficpi.db.models import Base

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations through trafficpi.db.upgrade_to_head()")

context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
