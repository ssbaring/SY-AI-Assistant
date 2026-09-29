"""weighing/ 아래 테스트에서만 쓰는 fixture.

여기 있는 fixture는 이 디렉터리 안의 테스트에만 자동 적용된다(루트
tests/conftest.py나 test_health.py에는 영향을 주지 않는다). 마이그레이션이
깨지더라도 0단계 헬스체크 테스트는 그대로 통과해야 하기 때문에 의도적으로
분리했다.
"""

from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

# 삭제 금지 트리거가 있는 테이블은 DELETE로 정리할 수 없다. 테스트가 끝날
# 때마다 트리거를 잠깐 끄고 TRUNCATE한 뒤 다시 켠다. _test DB에서만 실행되도록
# 세션 시작 시 한 번 더 확인한다.
_CLEANUP_SQL = """
ALTER TABLE weighing_records DISABLE TRIGGER trg_weighing_records_no_delete;
ALTER TABLE weighing_record_history DISABLE TRIGGER trg_weighing_record_history_no_delete;
TRUNCATE TABLE weighing_record_history, weighing_records, weighing_ticket_sequences;
ALTER TABLE weighing_records ENABLE TRIGGER trg_weighing_records_no_delete;
ALTER TABLE weighing_record_history ENABLE TRIGGER trg_weighing_record_history_no_delete;
"""


def _require_test_database(database_url: str) -> None:
    if not make_url(database_url).database.endswith("_test"):
        raise RuntimeError("weighing 테스트는 이름이 _test로 끝나는 DB에서만 실행할 수 있습니다.")


def resolved_host(host: str) -> str:
    """이 PC에서는 "localhost"가 IPv6(::1)로 먼저 풀릴 때 연결이 오래 멈추는
    경우가 있었다(Docker는 127.0.0.1에만 포트를 열어 둠). 매번 겪지 않도록
    테스트 연결에서는 항상 127.0.0.1을 명시한다."""

    return "127.0.0.1" if host == "localhost" else host


def connect(database_url: str) -> psycopg.Connection:
    url = make_url(database_url)
    return psycopg.connect(
        host=resolved_host(url.host),
        port=url.port,
        user=url.username,
        password=url.password,
        dbname=url.database,
        autocommit=True,
        connect_timeout=5,
    )


@pytest.fixture(scope="session", autouse=True)
def migrated_schema(test_database):
    """세션당 한 번, 테스트 DB를 최신 마이그레이션까지 올린다."""

    from app.core.config import settings

    _require_test_database(settings.database_url)
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def api_client(migrated_schema):
    """조회 API 테스트용 TestClient. 계근 스키마가 준비된 뒤에만 만든다."""

    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture
def raw_conn(migrated_schema):
    """psycopg 원시 연결. 트리거·동시성 등 ORM을 거치지 않는 검증에 쓴다."""

    from app.core.config import settings

    _require_test_database(settings.database_url)
    conn = connect(settings.database_url)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def clean_weighing_tables(migrated_schema):
    """각 테스트 뒤 계근 관련 테이블을 완전히 비운다(삭제 금지 트리거는 잠깐 끈다)."""

    from app.core.config import settings

    yield

    _require_test_database(settings.database_url)
    with connect(settings.database_url) as conn:
        conn.execute(_CLEANUP_SQL)
