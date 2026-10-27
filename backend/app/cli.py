import argparse
import getpass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import User
from app.schemas import UserCreate
from app.security import hash_password


def migration_config():
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "migrations"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate", "init-admin"])
    args = parser.parse_args()
    if args.action == "migrate":
        command.upgrade(migration_config(), "head")
        return
    settings = get_settings()
    data = UserCreate(
        email=input("Email: "),
        display_name=input("Display name: "),
        password=getpass.getpass("Password (at least 12 characters): "),
        role="admin",
    )
    if getpass.getpass("Repeat password: ") != data.password:
        raise SystemExit("Passwords do not match")
    engine = make_engine(settings.database_url)
    with make_session_factory(engine)() as db:
        if db.scalar(select(User.id).where(User.email == data.email)):
            raise SystemExit("Account already exists")
        db.add(
            User(
                email=data.email,
                display_name=data.display_name,
                role="admin",
                password_hash=hash_password(data.password),
            )
        )
        db.commit()
    engine.dispose()
    print("Administrator created")


if __name__ == "__main__":
    main()
