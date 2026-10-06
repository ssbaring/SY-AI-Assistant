"""연동 테스트용 도우미. backend 가상환경의 Python으로, backend/ 를 작업 폴더로 실행한다.

    <backend python> e2e_backend_helper.py prepare   # 테스트 DB 준비 + 시드, 결과를 JSON으로 출력
    <backend python> e2e_backend_helper.py serve PORT # 테스트 DB에 연결된 API를 127.0.0.1:PORT에 실행
    <backend python> e2e_backend_helper.py cleanup    # 테스트 DB의 계근 테이블 비우기

DB 주소는 backend 테스트와 같은 방식(tests/conftest.py)으로 정한다. 즉 이름이
_test로 끝나는 DB만 쓰고, 그렇지 않으면 실행을 거부한다. 개발 DB에는 접속하지
않는다. DB 주소(계정 포함)는 출력하지 않는다.
"""

import json
import os
import sys

# 이 파일이 있는 폴더(MCP 서버의 tests/) 대신 backend/ 를 import 기준으로 삼는다.
sys.path[0] = os.getcwd()

# app 모듈보다 먼저 불러와야 한다. DATABASE_URL을 테스트 DB로 바꾸고, 이름이
# _test로 끝나지 않으면 예외를 낸다.
import tests.conftest as backend_conftest  # noqa: E402


def _settings_url() -> str:
    from sqlalchemy.engine import make_url

    from app.core.config import settings

    database = make_url(settings.database_url).database or ""
    if not database.endswith("_test") or settings.database_url != backend_conftest.TEST_DATABASE_URL:
        raise RuntimeError("연동 테스트는 이름이 _test로 끝나는 DB에서만 실행할 수 있습니다.")
    return settings.database_url


def _ensure_test_database(database_url: str) -> None:
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    from tests.weighing.conftest import resolved_host

    url = make_url(database_url)
    with psycopg.connect(
        host=resolved_host(url.host),
        port=url.port,
        user=url.username,
        password=url.password,
        dbname="postgres",
        autocommit=True,
        connect_timeout=5,
    ) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))


def _clean(database_url: str) -> None:
    from tests.weighing.conftest import _CLEANUP_SQL, _require_test_database, connect

    _require_test_database(database_url)
    with connect(database_url) as conn:
        conn.execute(_CLEANUP_SQL)


def prepare() -> None:
    database_url = _settings_url()
    _ensure_test_database(database_url)

    from alembic import command
    from alembic.config import Config

    from tests.weighing.conftest import ALEMBIC_INI, connect
    from tests.weighing.test_weighing_records import cancel, complete_with_tare, insert_in_progress

    command.upgrade(Config(str(ALEMBIC_INI)), "head")
    _clean(database_url)

    with connect(database_url) as conn:
        completed_id, completed_ticket = insert_in_progress(
            conn,
            direction="INBOUND",
            partner_name="E2E-거래처A",
            item_name="E2E-폐합성수지",
            vehicle_no_raw="12가 3456",
            vehicle_no_norm="12가3456",
            gross_weight_kg=15000,
        )
        complete_with_tare(conn, completed_id, 5000)
        in_progress_id, in_progress_ticket = insert_in_progress(
            conn,
            direction="OUTBOUND",
            partner_name="E2E-거래처B",
            item_name="E2E-고철",
            vehicle_no_raw="34나 7890",
            vehicle_no_norm="34나7890",
            gross_weight_kg=20000,
        )
        canceled_id, canceled_ticket = insert_in_progress(
            conn,
            direction="INBOUND",
            partner_name="E2E-거래처A",
            item_name="E2E-폐목재",
            vehicle_no_raw="56다 1234",
            vehicle_no_norm="56다1234",
            gross_weight_kg=9000,
        )
        cancel(conn, canceled_id, cancelled_by="E2E-op", cancel_reason="E2E-reason")

    sys.stdout.write(
        json.dumps(
            {
                "completed": {"id": completed_id, "ticket_no": completed_ticket},
                "in_progress": {"id": in_progress_id, "ticket_no": in_progress_ticket},
                "canceled": {"id": canceled_id, "ticket_no": canceled_ticket},
            }
        )
    )


def serve(port: int) -> None:
    _settings_url()

    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")


def cleanup() -> None:
    _clean(_settings_url())


if __name__ == "__main__":
    command_name = sys.argv[1]
    if command_name == "prepare":
        prepare()
    elif command_name == "serve":
        serve(int(sys.argv[2]))
    elif command_name == "cleanup":
        cleanup()
    else:
        raise SystemExit(f"unknown command: {command_name}")
