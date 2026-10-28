from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect


def test_migration_upgrade_downgrade_matches_metadata(tmp_path):
    from app.db import make_engine
    from app.models import Base

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/migrations"))
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = make_engine(url)
    assert set(Base.metadata.tables).issubset(inspect(engine).get_table_names())
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()


def test_migration_check_preserves_framework_checkpoint_tables(tmp_path):
    from app.db import make_engine
    from sqlalchemy import text

    config = Config("alembic.ini")
    url = f"sqlite:///{tmp_path / 'framework.db'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = make_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE checkpoint_migrations (v INTEGER PRIMARY KEY)"))
    command.check(config)
    engine.dispose()
