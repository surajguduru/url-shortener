import secrets
import string
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import Base, engine, get_session
from app.models import Link

CODE_ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7
STATIC_DIR = Path(__file__).parent / "static"

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="URL Shortener", lifespan=lifespan)


class LinkCreate(BaseModel):
    url: HttpUrl


class LinkOut(BaseModel):
    code: str
    target_url: str
    clicks: int
    created_at: datetime


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/links", response_model=LinkOut, status_code=201)
def create_link(payload: LinkCreate, session: Session = Depends(get_session)):
    link = Link(code=generate_code(), target_url=str(payload.url), clicks=0)
    session.add(link)
    session.commit()
    session.refresh(link)
    print(f"created link {link.code} -> {link.target_url}")
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
        raise HTTPException(status_code=404, detail="Link not found")
    session.execute(update(Link).where(Link.id == link.id).values(clicks=Link.clicks + 1))
    session.commit()
    return RedirectResponse(link.target_url, status_code=307)
