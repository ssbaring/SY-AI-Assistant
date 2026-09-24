import os
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.engine import make_url

_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _build_test_database_url() -> str:
    base = os.environ.get("DATABASE_URL") or dotenv_values(_ENV_FILE).get("DATABASE_URL")
    if not base:
        raise RuntimeError("DATABASE_URL을 찾을 수 없습니다. backend/.env를 확인하세요.")
    url = make_url(base)
    if not url.database.endswith("_test"):
        url = url.set(database=f"{url.database}_test")
    return url.render_as_string(hide_password=False)


# app 모듈을 불러오기 전에 반드시 테스트 DB로 바꿔야 개발 DB에 접속하지 않는다.
TEST_DATABASE_URL = _build_test_database_url()
if not make_url(TEST_DATABASE_URL).database.endswith("_test"):
    raise RuntimeError("테스트는 이름이 _test로 끝나는 DB에서만 실행할 수 있습니다.")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


@pytest.fixture(scope="session", autouse=True)
def test_database():
    url = make_url(TEST_DATABASE_URL)
    with psycopg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        dbname="postgres",
        autocommit=True,
        connect_timeout=3,
    ) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)
        ).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)
