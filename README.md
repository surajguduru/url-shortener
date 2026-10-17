# URL Shortener

A small URL shortener built with FastAPI and PostgreSQL. Paste a long URL, get a
short code back; visiting `/<code>` redirects to the original URL and counts the click.

This repository is the subject of an Advanced DevOps final project. The commit
tagged `baseline` is the application **before** any DevOps work; all improvements
arrive in a single pull request on top of it.

## API

| Method | Path                | Description                          |
|--------|---------------------|--------------------------------------|
| GET    | `/`                 | Web page with a form to shorten URLs |
| POST   | `/api/links`        | Create a short link: `{"url": "https://..."}` → `201` |
| GET    | `/api/links/{code}` | Link details and click count         |
| GET    | `/{code}`           | `307` redirect to the target URL     |

## Running locally

Prerequisites: Python 3.12+ and a PostgreSQL server.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# Point the app at your database (this is the default if unset)
export DATABASE_URL=postgresql+psycopg://shortener:shortener@localhost:5432/shortener

.venv/bin/uvicorn app.main:app --port 8000
```

Open http://localhost:8000. Tables are created automatically on startup.

If you don't have Postgres installed, one way to get one:

```bash
docker run -d --name shortener-pg -p 5432:5432 \
  -e POSTGRES_USER=shortener -e POSTGRES_PASSWORD=shortener -e POSTGRES_DB=shortener \
  postgres:16-alpine
```

## Tests

```bash
.venv/bin/pytest
```

Tests use an in-memory SQLite database, so they need no running Postgres.

## Baseline: what already exists

At the baseline the project has only:

- The application code and a unit test suite (pytest).
- Manual local run instructions (this README).

It deliberately has **no** DevOps features yet:

- No container image (no Dockerfile).
- No Kubernetes manifests or Helm chart.
- No CI/CD pipeline; tests are run by hand.
- No health/readiness endpoints.
- No metrics, tracing, or structured logging (plain `print` and uvicorn access logs).
- Database credentials come from a single env var with a hard-coded default.
- Schema is created with `create_all` at startup; no migrations.
