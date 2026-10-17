import logging
import secrets
import string
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import database_is_reachable, engine, get_session
from app.models import Link
from app.observability import (
    LINKS_CREATED,
    REDIRECTS,
    configure_logging,
    configure_tracing,
    install_request_middleware,
    start_metrics_server,
)

CODE_ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7
STATIC_DIR = Path(__file__).parent / "static"

configure_logging()
logger = logging.getLogger("shortener")

# Schema changes are applied by Alembic (a separate migration Job), not at startup,
# so several replicas starting at once never race on DDL.
app = FastAPI(title="URL Shortener", version=settings.app_version)
install_request_middleware(app)
configure_tracing(app, engine)
start_metrics_server()


class LinkCreate(BaseModel):
    url: HttpUrl


class LinkOut(BaseModel):
    code: str
    target_url: str
    clicks: int
    created_at: datetime


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


@app.get("/healthz", include_in_schema=False)
def healthz():
    # Liveness: the process can serve requests. Deliberately ignores the database,
    # so a database outage doesn't make Kubernetes restart every app pod.
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def readyz():
    # Readiness: only receive traffic while the database is reachable.
    if not database_is_reachable():
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return {"status": "ok", "database": "up"}


@app.get("/version")
def version():
    return {"version": settings.app_version, "commit": settings.git_commit}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/links", response_model=LinkOut, status_code=201)
def create_link(payload: LinkCreate, session: Session = Depends(get_session)):
    link = Link(code=generate_code(), target_url=str(payload.url), clicks=0)
    session.add(link)
    session.commit()
    session.refresh(link)
    LINKS_CREATED.inc()
    logger.info("link created", extra={"code": link.code})
    return link


@app.get("/api/links/{code}", response_model=LinkOut)
def get_link(code: str, session: Session = Depends(get_session)):
    link = session.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    return link


@app.get("/{code}", include_in_schema=False)
def redirect(code: str, session: Session = Depends(get_session)):
    link = session.scalar(select(Link).where(Link.code == code))
    if link is None:
        REDIRECTS.labels("miss").inc()
        raise HTTPException(status_code=404, detail="Link not found")
    # Read before commit: commit expires the object, and touching it afterwards
    # costs a second SELECT (spotted in a Jaeger trace of this endpoint).
    target_url = link.target_url
    session.execute(update(Link).where(Link.id == link.id).values(clicks=Link.clicks + 1))
    session.commit()
    REDIRECTS.labels("hit").inc()
    return RedirectResponse(target_url, status_code=307)
