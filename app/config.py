import os
from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_version: str
    git_commit: str
    log_level: str
    metrics_port: int | None


def _database_url() -> str:
    # A full URL wins (tests, local dev); in Kubernetes the parts come from a
    # ConfigMap (host, port, name) and a Secret (user, password).
    if url := os.getenv("DATABASE_URL"):
        return url
    user = os.environ["DB_USER"]
    password = quote(os.environ["DB_PASSWORD"], safe="")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "shortener")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


def load_settings() -> Settings:
    return Settings(
        database_url=_database_url(),
        app_version=os.getenv("APP_VERSION", "dev"),
        git_commit=os.getenv("GIT_COMMIT", "unknown"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        metrics_port=int(port) if (port := os.getenv("METRICS_PORT")) else None,
    )


settings = load_settings()
