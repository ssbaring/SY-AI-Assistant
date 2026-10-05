"""MCP 서버 테스트 공용 fixture.

여기 있는 테스트는 DB에 접속하지 않는다. API는 MockTransport 또는 임시 포트의
가짜 HTTP 서버로 대신한다(실제 backend 연동은 test_e2e_backend.py만 한다).
"""

import json
import sys
import threading
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECORDS_PATH = "/api/v1/weighing-records"

# 가짜 API의 특수 경로(ID). 둘 다 Content-Length 없이 응답한다.
DRIP_RECORD_ID = 901  # 0.1초마다 1바이트씩 끝없이 보낸다(read 타임아웃에는 걸리지 않는다).
OVERSIZED_RECORD_ID = 902  # 상한(5MB)보다 훨씬 큰 본문을 보낸다.
OVERSIZED_BODY_BYTES = 40 * 1024 * 1024

# 가짜 API가 500 응답 본문에 넣는 내부정보. 도구 결과에 나오면 안 된다.
INTERNAL_MARKERS = ("Traceback", "SELECT", "psycopg", "fakeuser:fakepass@", "weighing_records_query.py")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_record(**overrides):
    record = {
        "id": 1,
        "ticket_no": "20261001-0001",
        "direction": "INBOUND",
        "status": "COMPLETED",
        "vehicle_no": "12가 3456",
        "partner_name": "TEST-거래처",
        "item_name": "TEST-품목",
        "operator_name": "TEST-담당",
        "gross_weight_kg": 15000,
        "tare_weight_kg": 5000,
        "net_weight_kg": 10000,
        "first_weighed_at": "2026-10-01T00:30:00+00:00",
        "created_at": "2026-10-01T00:30:00+00:00",
        "updated_at": "2026-10-01T01:00:00+00:00",
    }
    record.update(overrides)
    return record


def make_detail(**overrides):
    detail = make_record(cancelled_at=None, cancelled_by=None, cancel_reason=None)
    detail.update(overrides)
    return detail


def make_list(items=None, **overrides):
    items = [make_record()] if items is None else items
    body = {"items": items, "page": 1, "page_size": 20, "total": len(items), "total_pages": 1 if items else 0}
    body.update(overrides)
    return body


class _StubApiHandler(BaseHTTPRequestHandler):
    """조회 API를 흉내 내는 가짜 서버. 경로 값에 따라 정해진 응답을 준다."""

    def log_message(self, *_args):  # 테스트 출력에 접속 로그를 남기지 않는다.
        pass

    def _send(self, status, body=None, headers=None):
        payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _record_and_route(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        self.server.requests.append((self.command, parts.path, query))

        if self.command != "GET":
            return self._send(405, {"detail": "Method Not Allowed"})
        if parts.path == "/canary":
            return self._send(200, make_detail(id=666, ticket_no="REDIRECTED"))
        if parts.path == RECORDS_PATH:
            if query.get("page") == ["0"]:
                return self._send(
                    422,
                    {
                        "detail": [
                            {
                                "type": "greater_than_equal",
                                "loc": ["query", "page"],
                                "msg": "Input should be greater than or equal to 1",
                                "input": "0",
                                "ctx": {"ge": 1},
                                "url": "https://errors.pydantic.dev/2.13/v/greater_than_equal",
                            }
                        ]
                    },
                )
            return self._send(200, make_list())
        if parts.path == f"{RECORDS_PATH}/ticket/20261001-0001":
            return self._send(200, make_detail())
        if parts.path.startswith(f"{RECORDS_PATH}/ticket/"):
            return self._send(404, {"detail": "계근번호에 해당하는 기록을 찾을 수 없습니다"})

        record_id = parts.path.removeprefix(f"{RECORDS_PATH}/")
        if record_id == "1":
            return self._send(200, make_detail())
        if record_id == "302":
            return self._send(302, None, {"Location": "/canary"})
        if record_id == "500":
            return self._send(
                500,
                {
                    "detail": "Traceback (most recent call last): weighing_records_query.py "
                    "psycopg.errors: SELECT * FROM weighing_records -- postgresql://fakeuser:fakepass@db"
                },
            )
        if record_id == "777":
            broken = make_detail()
            del broken["net_weight_kg"]
            return self._send(200, broken)
        if record_id == str(DRIP_RECORD_ID):
            return self._stream_without_length("drip", chunk=b" ", total=None, interval=0.1)
        if record_id == str(OVERSIZED_RECORD_ID):
            return self._stream_without_length(
                "oversized", chunk=b" " * 65536, total=OVERSIZED_BODY_BYTES, interval=0
            )
        return self._send(404, {"detail": "계근기록을 찾을 수 없습니다"})

    def _stream_without_length(self, label, *, chunk, total, interval):
        """Content-Length 없이 본문을 흘려보낸다(HTTP/1.0, 연결을 닫아야 끝난다).

        클라이언트가 먼저 연결을 끊으면 쓰기가 실패한다. 그 사실과 그때까지 보낸
        바이트 수를 기록해 두어, 테스트가 "연결이 실제로 닫혔는지" 확인할 수 있게 한다.
        """

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        sent = 0
        deadline = time.monotonic() + 40
        try:
            while (total is None or sent < total) and time.monotonic() < deadline:
                self.wfile.write(chunk)
                self.wfile.flush()
                sent += len(chunk)
                if interval:
                    time.sleep(interval)
        except OSError:
            self.server.events.append((f"{label}-aborted", sent))
        else:
            self.server.events.append((f"{label}-finished", sent))

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _record_and_route


class StubApi:
    def __init__(self, server):
        self._server = server
        self.base_url = f"http://127.0.0.1:{server.server_address[1]}"

    @property
    def requests(self):
        return self._server.requests

    def wait_for_event(self, label, timeout=10):
        """가짜 서버가 남긴 (label, 보낸 바이트 수) 기록을 기다려 바이트 수를 돌려준다."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for name, sent in list(self._server.events):
                if name == label:
                    return sent
            time.sleep(0.05)
        raise AssertionError(f"{label} 기록이 없습니다: {self._server.events}")


@pytest.fixture
def stub_api():
    """임시 포트(127.0.0.1:0)에 가짜 API를 띄우고, 테스트가 끝나면 반드시 내린다."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubApiHandler)
    server.daemon_threads = True
    server.requests = []
    server.events = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield StubApi(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@asynccontextmanager
async def mcp_session(base_url):
    """MCP 서버를 실제 자식 프로세스로 띄우고 stdio로 연결한다.

    블록을 벗어나면(테스트 실패 포함) SDK의 stdio_client가 stdin을 닫고, 종료되지
    않으면 프로세스 트리를 강제 종료한다.
    """

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "factory_api_mcp"],
        env={"FACTORY_API_BASE_URL": base_url},
        cwd=PROJECT_ROOT,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
            yield session


def result_text(result):
    return "".join(block.text for block in result.content if block.type == "text")


def assert_no_internal_details(text):
    for marker in INTERNAL_MARKERS:
        assert marker not in text
    assert "127.0.0.1" not in text
