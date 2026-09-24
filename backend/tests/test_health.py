import importlib
import time

import pytest
from sqlalchemy import text

DB_ERROR_BODY = {"status": "error", "detail": "database unavailable"}
LEAK_HOST = "192.0.2.1"  # RFC 5737 문서용 주소: 실제로 응답하는 서버가 없다
LEAK_PORT = "54321"
LEAK_USER = "leak_user"
LEAK_PASSWORD = "leak_pw"
LEAK_DB = "leak_db"


@pytest.fixture
def unreachable_db(monkeypatch):
    import app.core.database as database
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "database_url",
        f"postgresql+psycopg://{LEAK_USER}:{LEAK_PASSWORD}@{LEAK_HOST}:{LEAK_PORT}/{LEAK_DB}",
    )
    # reload는 같은 모듈 딕셔너리를 다시 채우므로 app.main.check_connection도 새 engine을 쓰게 된다.
    importlib.reload(database)
    yield
    monkeypatch.undo()
    importlib.reload(database)


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_db_ok(client):
    response = client.get("/health/db")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_tests_use_separate_test_database():
    from app.core.database import engine

    with engine.connect() as conn:
        name = conn.execute(text("SELECT current_database()")).scalar()
    assert name.endswith("_test")


def test_health_db_fails_fast_when_db_unreachable(client, unreachable_db):
    started = time.monotonic()
    response = client.get("/health/db")
    elapsed = time.monotonic() - started

    assert response.json() == DB_ERROR_BODY
    assert elapsed < 10


def test_health_db_error_does_not_leak_internal_details(client, unreachable_db):
    body = client.get("/health/db").text

    for secret in (LEAK_HOST, LEAK_PORT, LEAK_USER, LEAK_PASSWORD, LEAK_DB, "psycopg", "timeout"):
        assert secret not in body


def test_health_db_recovers_after_unreachable_db(client):
    response = client.get("/health/db")
    assert response.json() == {"status": "ok"}
