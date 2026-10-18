import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app


@pytest.fixture
def client():
    test_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False)

    def override_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    # Not using the context manager skips the lifespan hook, which targets Postgres.
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_link_returns_code(client):
    response = client.post("/api/links", json={"url": "https://example.com/page"})
    assert response.status_code == 201
    body = response.json()
    assert len(body["code"]) == 7
    assert body["target_url"] == "https://example.com/page"
    assert body["clicks"] == 0


def test_redirect_and_click_count(client):
    code = client.post("/api/links", json={"url": "https://example.com/"}).json()["code"]

    response = client.get(f"/{code}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/"

    assert client.get(f"/api/links/{code}").json()["clicks"] == 1


def test_unknown_code_returns_404(client):
    assert client.get("/doesnotexist", follow_redirects=False).status_code == 404
    assert client.get("/api/links/doesnotexist").status_code == 404


def test_invalid_url_rejected(client):
    response = client.post("/api/links", json={"url": "not-a-url"})
    assert response.status_code == 422


def test_index_page_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "URL Shortener" in response.text


def test_healthz_does_not_need_database(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_reports_database_down(client, monkeypatch):
    monkeypatch.setattr("app.main.database_is_reachable", lambda: False)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["database"] == "down"


def test_readyz_ok_when_database_up(client, monkeypatch):
    monkeypatch.setattr("app.main.database_is_reachable", lambda: True)
    assert client.get("/readyz").status_code == 200


def test_version_is_reported_in_body_and_header(client):
    response = client.get("/version")
    assert response.json()["version"] == "test"
    assert response.headers["x-app-version"] == "test"


def test_metrics_use_route_templates(client):
    code = client.post("/api/links", json={"url": "https://example.com/"}).json()["code"]
    client.get(f"/{code}", follow_redirects=False)

    metrics = generate_latest().decode()
    assert 'http_requests_total{method="GET",route="/{code}",status="307"}' in metrics
    assert f'route="/{code}"' not in metrics
    assert "shortener_links_created_total" in metrics
    assert 'shortener_redirects_total{result="hit"}' in metrics
