"""실제 backend 연동 테스트.

실제 FastAPI 앱을 테스트 DB(이름이 _test로 끝나는 DB)에 연결해 임시 포트에 띄우고,
MCP 서버를 자식 프로세스로 실행해 도구 결과를 API 직접 호출 결과와 비교한다.

- 개발 DB에는 접속하지 않는다. DB 주소는 backend 테스트와 같은 규칙으로 정하고,
  도우미(e2e_backend_helper.py)가 _test 여부를 다시 확인한다.
- 시드 데이터는 테스트 DB에만 넣고, 끝나면 비운다.
- backend 가상환경이나 DB가 없으면 건너뛴다(-rs로 사유 확인). 건너뛴 실행은
  연동 검증을 한 것이 아니다.
"""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest

from factory_api_mcp.api_client import MSG_INVALID_INPUT, MSG_NOT_FOUND
from tests.conftest import mcp_session, result_text

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "backend"
BACKEND_PYTHON = BACKEND_DIR / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
HELPER = Path(__file__).with_name("e2e_backend_helper.py")
RECORDS_PATH = "/api/v1/weighing-records"


def _run_helper(*args):
    return subprocess.run(
        [str(BACKEND_PYTHON), str(HELPER), *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        timeout=120,
    )


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture(scope="module")
def backend():
    """(API 기본 주소, 시드 정보). 끝나면 서버를 내리고 테스트 DB를 비운다."""

    if not BACKEND_PYTHON.exists():
        pytest.skip(f"backend 가상환경이 없습니다: {BACKEND_PYTHON}")

    prepared = _run_helper("prepare")
    if prepared.returncode != 0:
        # 도우미의 오류 출력에는 DB 접속정보가 섞일 수 있어 마지막 줄의 예외 이름만 보여준다.
        last_line = prepared.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        reason = last_line[0].split(":")[0] if last_line else "unknown"
        pytest.skip(f"테스트 DB를 준비하지 못했습니다({reason}). PostgreSQL 컨테이너를 확인하세요.")
    seeds = json.loads(prepared.stdout)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [str(BACKEND_PYTHON), str(HELPER), "serve", str(port)],
        cwd=BACKEND_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        with httpx2.Client(base_url=base_url, trust_env=False, timeout=2) as probe:
            while True:
                if server.poll() is not None:
                    pytest.fail("backend 서버가 시작 중에 종료되었습니다.")
                try:
                    if probe.get("/health/db").json() == {"status": "ok"}:
                        break
                except httpx2.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    pytest.fail("backend 서버가 30초 안에 준비되지 않았습니다.")
                time.sleep(0.2)
        yield base_url, seeds
    finally:
        _stop(server)
        cleaned = _run_helper("cleanup")
        assert cleaned.returncode == 0, "테스트 DB 정리에 실패했습니다."


@pytest.fixture
def api(backend):
    base_url, _seeds = backend
    with httpx2.Client(base_url=base_url, trust_env=False, timeout=10) as client:
        yield client


async def test_list_matches_the_api(backend, api):
    base_url, seeds = backend

    async with mcp_session(base_url) as session:
        await session.initialize()
        everything = await session.call_tool("list_weighing_records", {})
        filtered = await session.call_tool(
            "list_weighing_records",
            {"direction": "INBOUND", "partner_name": "e2e-거래처a", "sort_dir": "asc", "page_size": 1},
        )
        by_vehicle = await session.call_tool("list_weighing_records", {"vehicle_number": "34나-7890"})
        canceled_only = await session.call_tool("list_weighing_records", {"status": "CANCELED"})
        future = await session.call_tool(
            "list_weighing_records", {"started_from": "2999-01-01T00:00:00+09:00"}
        )

    assert everything.is_error is False
    assert everything.structured_content == api.get(RECORDS_PATH).json()
    assert everything.structured_content["total"] == 3

    expected_filtered = api.get(
        RECORDS_PATH,
        params={"direction": "INBOUND", "partner_name": "e2e-거래처a", "sort_dir": "asc", "page_size": 1},
    ).json()
    assert filtered.structured_content == expected_filtered
    assert filtered.structured_content["total"] == 2
    assert filtered.structured_content["total_pages"] == 2
    assert len(filtered.structured_content["items"]) == 1

    assert [item["id"] for item in by_vehicle.structured_content["items"]] == [seeds["in_progress"]["id"]]
    assert [item["id"] for item in canceled_only.structured_content["items"]] == [seeds["canceled"]["id"]]
    assert "cancel_reason" not in canceled_only.structured_content["items"][0]
    assert future.structured_content == {
        "items": [],
        "page": 1,
        "page_size": 20,
        "total": 0,
        "total_pages": 0,
    }


async def test_detail_lookups_match_the_api(backend, api):
    base_url, seeds = backend
    completed, in_progress, canceled = seeds["completed"], seeds["in_progress"], seeds["canceled"]

    async with mcp_session(base_url) as session:
        await session.initialize()
        by_id = await session.call_tool("get_weighing_record_by_id", {"record_id": completed["id"]})
        by_ticket = await session.call_tool(
            "get_weighing_record_by_ticket", {"ticket_no": completed["ticket_no"]}
        )
        pending = await session.call_tool("get_weighing_record_by_id", {"record_id": in_progress["id"]})
        canceled_detail = await session.call_tool(
            "get_weighing_record_by_ticket", {"ticket_no": canceled["ticket_no"]}
        )

    expected = api.get(f"{RECORDS_PATH}/{completed['id']}").json()
    assert by_id.is_error is False
    assert by_id.structured_content == expected
    assert by_ticket.structured_content == expected
    assert expected["gross_weight_kg"] == 15000
    assert expected["tare_weight_kg"] == 5000
    assert expected["net_weight_kg"] == 10000
    assert expected["vehicle_no"] == "12가 3456"
    assert expected["created_at"].endswith("+00:00")

    assert pending.structured_content == api.get(f"{RECORDS_PATH}/{in_progress['id']}").json()
    assert pending.structured_content["status"] == "IN_PROGRESS"
    assert pending.structured_content["net_weight_kg"] is None

    assert canceled_detail.structured_content == api.get(f"{RECORDS_PATH}/{canceled['id']}").json()
    assert canceled_detail.structured_content["status"] == "CANCELED"
    assert canceled_detail.structured_content["cancel_reason"] == "E2E-reason"
    assert canceled_detail.structured_content["cancelled_by"] == "E2E-op"


async def test_api_errors_reach_the_tool_result(backend, api):
    base_url, _seeds = backend

    async with mcp_session(base_url) as session:
        await session.initialize()
        missing_id = await session.call_tool("get_weighing_record_by_id", {"record_id": 987654321})
        missing_ticket = await session.call_tool("get_weighing_record_by_ticket", {"ticket_no": "19990101-9999"})
        naive_date = await session.call_tool("list_weighing_records", {"started_from": "2026-10-01T00:00:00"})
        reversed_range = await session.call_tool(
            "list_weighing_records",
            {"started_from": "2026-10-02T00:00:00+09:00", "started_to": "2026-10-01T00:00:00+09:00"},
        )
        too_large = await session.call_tool("list_weighing_records", {"page_size": 101})
        blank = await session.call_tool("list_weighing_records", {"partner_name": "   "})

    assert api.get(f"{RECORDS_PATH}/987654321").status_code == 404
    for result in (missing_id, missing_ticket):
        assert result.is_error is True
        assert result_text(result) == MSG_NOT_FOUND

    assert api.get(RECORDS_PATH, params={"page_size": 101}).status_code == 422
    for result, field in (
        (naive_date, "started_from"),
        (reversed_range, "started_from"),
        (too_large, "page_size"),
        (blank, "partner_name"),
    ):
        text = result_text(result)
        assert result.is_error is True
        assert text.startswith(MSG_INVALID_INPUT)
        assert field in text
        for hidden in ("Traceback", "SELECT", "psycopg", "errors.pydantic.dev", "127.0.0.1"):
            assert hidden not in text
