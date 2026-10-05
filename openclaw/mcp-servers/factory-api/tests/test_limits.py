"""전체 시간 상한(15초)과 응답 크기 상한(5MB).

앞부분은 MockTransport로 동작을 세밀하게 확인하고, 뒷부분은 실제 소켓(임시 포트의
가짜 API)과 실제 MCP 자식 프로세스로 연결이 정말 닫히는지 확인한다.
"""

import json
import time

import anyio
import httpx2
import pytest

from factory_api_mcp import api_client
from factory_api_mcp.api_client import ApiError, FactoryApiClient
from tests.conftest import (
    DRIP_RECORD_ID,
    OVERSIZED_BODY_BYTES,
    OVERSIZED_RECORD_ID,
    make_detail,
    make_list,
    mcp_session,
    result_text,
)

pytestmark = pytest.mark.anyio

BASE_URL = "http://127.0.0.1:8000"


class RecordingStream(httpx2.AsyncByteStream):
    """조각을 하나씩 내보내며, 몇 개가 읽혔는지와 닫혔는지를 기록한다.

    Content-Length를 알 수 없는 응답과 같다(httpx2가 헤더를 만들지 않는다).
    """

    def __init__(self, chunks, *, delay=0.0):
        self._chunks = chunks
        self._delay = delay
        self.chunks_read = 0
        self.bytes_read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            if self._delay:
                await anyio.sleep(self._delay)
            self.chunks_read += 1
            self.bytes_read += len(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


def endless(chunk):
    while True:
        yield chunk


def track_clients(monkeypatch):
    created = []
    real_client = httpx2.AsyncClient

    def tracking(**kwargs):
        client = real_client(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(api_client.httpx2, "AsyncClient", tracking)
    return created


def client_for(handler):
    return FactoryApiClient(BASE_URL, transport=httpx2.MockTransport(handler))


# ---------------------------------------------------------------------------
# 설정값
# ---------------------------------------------------------------------------


def test_limits_are_the_approved_values():
    assert api_client.TOTAL_TIMEOUT_SECONDS == 15.0
    assert api_client.CONNECT_TIMEOUT_SECONDS == 3.0
    assert api_client.READ_TIMEOUT_SECONDS == 10.0
    assert api_client.MAX_RESPONSE_BYTES == 5 * 1024 * 1024


async def test_compression_is_not_requested():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, json=make_list())

    await client_for(handler).list_weighing_records({})

    assert seen[0].headers["accept-encoding"] == "identity"


# ---------------------------------------------------------------------------
# 전체 시간 상한
# ---------------------------------------------------------------------------


async def test_total_timeout_while_waiting_for_the_response(monkeypatch):
    monkeypatch.setattr(api_client, "TOTAL_TIMEOUT_SECONDS", 0.2)
    created = track_clients(monkeypatch)

    async def handler(_request):
        await anyio.sleep(60)
        return httpx2.Response(200, json=make_list())

    started = time.monotonic()
    with pytest.raises(ApiError) as excinfo:
        await client_for(handler).list_weighing_records({})

    assert excinfo.value.message == api_client.MSG_TIMEOUT
    assert time.monotonic() - started < 5
    assert [client.is_closed for client in created] == [True]


async def test_total_timeout_covers_a_slowly_dripping_body(monkeypatch):
    """조각 사이 간격은 read 타임아웃(10초)보다 훨씬 짧지만, 전체 상한에는 걸린다."""

    monkeypatch.setattr(api_client, "TOTAL_TIMEOUT_SECONDS", 0.3)
    created = track_clients(monkeypatch)
    stream = RecordingStream(endless(b" "), delay=0.02)

    started = time.monotonic()
    with pytest.raises(ApiError) as excinfo:
        await client_for(lambda _r: httpx2.Response(200, stream=stream)).get_weighing_record_by_id(1)

    assert excinfo.value.message == api_client.MSG_TIMEOUT
    assert time.monotonic() - started < 5
    assert stream.chunks_read > 0  # 본문을 읽던 중에 끊겼다.
    assert stream.closed is True
    assert [client.is_closed for client in created] == [True]


async def test_response_finishing_within_the_limit_is_not_affected(monkeypatch):
    monkeypatch.setattr(api_client, "TOTAL_TIMEOUT_SECONDS", 5)
    body = json.dumps(make_detail()).encode()
    stream = RecordingStream([body[:10], body[10:]], delay=0.05)

    result = await client_for(lambda _r: httpx2.Response(200, stream=stream)).get_weighing_record_by_id(1)

    assert result.id == 1
    assert stream.closed is True


async def test_cancelled_call_closes_response_and_client(monkeypatch):
    """호출자가 취소하면(상한 전) ApiError로 바꾸지 않고 취소를 그대로 전파하며 정리한다."""

    created = track_clients(monkeypatch)
    stream = RecordingStream(endless(b" "), delay=0.02)
    client = client_for(lambda _r: httpx2.Response(200, stream=stream))

    with anyio.move_on_after(0.3) as scope:
        await client.get_weighing_record_by_id(1)

    assert scope.cancelled_caught is True
    assert stream.chunks_read > 0
    assert stream.closed is True
    assert [c.is_closed for c in created] == [True]


# ---------------------------------------------------------------------------
# 응답 크기 상한: 읽는 도중, 실제로 받은 바이트 기준
# ---------------------------------------------------------------------------


async def test_reading_stops_as_soon_as_the_limit_is_exceeded(monkeypatch):
    monkeypatch.setattr(api_client, "MAX_RESPONSE_BYTES", 10 * 1024)
    created = track_clients(monkeypatch)
    stream = RecordingStream(endless(b" " * 1024))
    response = httpx2.Response(200, stream=stream)
    assert "content-length" not in response.headers

    with pytest.raises(ApiError) as excinfo:
        await client_for(lambda _r: response).list_weighing_records({})

    assert excinfo.value.message == api_client.MSG_BAD_RESPONSE
    # 끝없는 본문인데도 상한을 넘긴 바로 그 조각(11번째)에서 멈췄다.
    assert stream.chunks_read == 11
    assert stream.bytes_read == 11 * 1024
    assert stream.closed is True
    assert [client.is_closed for client in created] == [True]


async def test_content_length_header_is_not_trusted(monkeypatch):
    monkeypatch.setattr(api_client, "MAX_RESPONSE_BYTES", 10 * 1024)
    stream = RecordingStream(endless(b" " * 1024))
    response = httpx2.Response(200, stream=stream, headers={"Content-Length": "100"})

    with pytest.raises(ApiError) as excinfo:
        await client_for(lambda _r: response).list_weighing_records({})

    assert excinfo.value.message == api_client.MSG_BAD_RESPONSE
    assert stream.chunks_read == 11


async def test_body_of_exactly_the_limit_is_accepted(monkeypatch):
    body = json.dumps(make_detail()).encode()
    padded = body + b" " * 100
    monkeypatch.setattr(api_client, "MAX_RESPONSE_BYTES", len(padded))
    stream = RecordingStream([padded[:50], padded[50:]])

    result = await client_for(lambda _r: httpx2.Response(200, stream=stream)).get_weighing_record_by_id(1)

    assert result.id == 1

    monkeypatch.setattr(api_client, "MAX_RESPONSE_BYTES", len(padded) - 1)
    with pytest.raises(ApiError):
        await client_for(
            lambda _r: httpx2.Response(200, stream=RecordingStream([padded]))
        ).get_weighing_record_by_id(1)


async def test_limit_also_applies_to_422_bodies(monkeypatch):
    monkeypatch.setattr(api_client, "MAX_RESPONSE_BYTES", 10 * 1024)
    stream = RecordingStream(endless(b" " * 1024))

    with pytest.raises(ApiError) as excinfo:
        await client_for(lambda _r: httpx2.Response(422, stream=stream)).list_weighing_records({})

    assert excinfo.value.message == api_client.MSG_BAD_RESPONSE
    assert stream.chunks_read == 11
    assert stream.closed is True


@pytest.mark.parametrize("status", [302, 404, 500])
async def test_bodies_of_other_statuses_are_never_read(status):
    stream = RecordingStream(endless(b"Traceback SELECT " * 100))

    with pytest.raises(ApiError):
        await client_for(lambda _r: httpx2.Response(status, stream=stream)).get_weighing_record_by_id(1)

    assert stream.chunks_read == 0
    assert stream.closed is True


# ---------------------------------------------------------------------------
# 실제 소켓: 연결이 정말 닫히는지
# ---------------------------------------------------------------------------


async def test_real_socket_oversized_body_without_content_length(stub_api):
    """상한은 5MB 그대로. 서버는 Content-Length 없이 40MB를 보내려 한다."""

    client = FactoryApiClient(stub_api.base_url)

    with pytest.raises(ApiError) as excinfo:
        await client.get_weighing_record_by_id(OVERSIZED_RECORD_ID)

    assert excinfo.value.message == api_client.MSG_BAD_RESPONSE
    # 클라이언트가 연결을 끊어 서버의 쓰기가 실패했고, 전체를 다 보내지 못했다.
    sent = stub_api.wait_for_event("oversized-aborted")
    assert sent < OVERSIZED_BODY_BYTES


async def test_real_socket_total_timeout_closes_the_connection(stub_api, monkeypatch):
    monkeypatch.setattr(api_client, "TOTAL_TIMEOUT_SECONDS", 0.5)
    client = FactoryApiClient(stub_api.base_url)

    started = time.monotonic()
    with pytest.raises(ApiError) as excinfo:
        await client.get_weighing_record_by_id(DRIP_RECORD_ID)

    assert excinfo.value.message == api_client.MSG_TIMEOUT
    assert time.monotonic() - started < 5
    stub_api.wait_for_event("drip-aborted")


async def test_real_socket_cancellation_closes_the_connection(stub_api):
    client = FactoryApiClient(stub_api.base_url)

    with anyio.move_on_after(0.5) as scope:
        await client.get_weighing_record_by_id(DRIP_RECORD_ID)

    assert scope.cancelled_caught is True
    stub_api.wait_for_event("drip-aborted")


# ---------------------------------------------------------------------------
# 실제 MCP 자식 프로세스: 상한값을 바꾸지 않은 그대로
# ---------------------------------------------------------------------------


async def test_mcp_server_enforces_size_limit(stub_api):
    async with mcp_session(stub_api.base_url) as session:
        await session.initialize()
        oversized = await session.call_tool("get_weighing_record_by_id", {"record_id": OVERSIZED_RECORD_ID})
        still_works = await session.call_tool("get_weighing_record_by_id", {"record_id": 1})

    assert oversized.is_error is True
    assert result_text(oversized) == api_client.MSG_BAD_RESPONSE
    assert stub_api.wait_for_event("oversized-aborted") < OVERSIZED_BODY_BYTES
    assert still_works.is_error is False


async def test_mcp_server_enforces_the_15_second_total_timeout(stub_api):
    """실제로 15초를 기다린다. 0.1초마다 1바이트가 오므로 read 타임아웃(10초)에는 걸리지 않는다."""

    async with mcp_session(stub_api.base_url) as session:
        await session.initialize()
        started = time.monotonic()
        dripping = await session.call_tool("get_weighing_record_by_id", {"record_id": DRIP_RECORD_ID})
        elapsed = time.monotonic() - started
        still_works = await session.call_tool("get_weighing_record_by_id", {"record_id": 1})

    assert dripping.is_error is True
    assert result_text(dripping) == api_client.MSG_TIMEOUT
    assert 14 <= elapsed <= 20
    # 서버 쪽에서 본 결과: 클라이언트가 15초 무렵 연결을 끊었다(0.1초당 1바이트).
    sent = stub_api.wait_for_event("drip-aborted")
    assert 100 <= sent <= 200
    assert still_works.is_error is False
