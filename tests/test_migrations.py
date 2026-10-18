import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_alembic_cli_creates_schema(tmp_path):
    # Runs the CLI exactly as the Kubernetes migration Job does.
    database_file = tmp_path / "migrate.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{database_file}"}
    alembic = Path(sys.executable).parent / "alembic"

    subprocess.run([alembic, "upgrade", "head"], cwd=REPO_ROOT, env=env, check=True)

    with sqlite3.connect(database_file) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"links", "alembic_version", "ix_links_code"} <= tables
