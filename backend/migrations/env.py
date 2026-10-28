from alembic import context
from app.models import Base
from sqlalchemy import engine_from_config, pool

config = context.config
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # LangGraph owns and migrates these tables separately from application metadata.
    framework = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
    return not (type_ == "table" and reflected and name in framework)


if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()
