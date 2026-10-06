"""FactoryApiClient 단위 테스트. 네트워크 대신 httpx2.MockTransport를 쓴다."""

import httpx2
import pytest

from factory_api_mcp import api_client
from factory_api_mcp.api_client import ApiError, FactoryApiClient
from tests.conftest import RECORDS_PATH, assert_no_internal_details, make_detail, make_list, make_record

pytestmark = pytest.mark.anyio

BASE_URL = "http://127.0.0.1:8000"


def make_client(handler):
    """(client, 보낸 요청 목록)을 돌려준다."""

    seen = []

    def recording_handler(request):
        seen.append(request)
        return handler(request)

    return FactoryApiClient(BASE_URL, transport=httpx2.MockTransport(recording_handler)), seen


def respond(status, json=None, **kwargs):
    return lambda _request: httpx2.Response(status, json=json, **kwargs)


# ---------------------------------------------------------------------------
# 요청 형태
# ---------------------------------------------------------------------------


async def test_list_sends_get_with_given_params_only():
    client, seen = make_client(respond(200, make_list()))

    await client.list_weighing_records({"direction": "INBOUND", "page": 2, "partner_name": "가나 다"})

    (request,) = seen
    assert request.method == "GET"
    assert request.url.host == "127.0.0.1"
    assert request.url.port == 8000
    assert request.url.path == RECORDS_PATH
    assert dict(request.url.params) == {"direction": "INBOUND", "page": "2", "partner_name": "가나 다"}
    assert request.content == b""


async def test_list_without_filters_sends_no_query():
    client, seen = make_client(respond(200, make_list()))
    await client.list_weighing_records({})
    assert seen[0].url.query == b""


async def test_detail_paths():
    client, seen = make_client(respond(200, make_detail()))

    await client.get_weighing_record_by_id(42)
    await client.get_weighing_record_by_ticket("20261001-0001")

    assert [r.method for r in seen] == ["GET", "GET"]
    assert seen[0].url.path == f"{RECORDS_PATH}/42"
    assert seen[1].url.path == f"{RECORDS_PATH}/ticket/20261001-0001"


async def test_ticket_no_is_encoded_as_a_single_path_segment():
    client, seen = make_client(respond(200, make_detail()))

    await client.get_weighing_record_by_ticket("A?b=1#c%2F d")

    request = seen[0]
    assert request.url.raw_path == f"{RECORDS_PATH}/ticket/A%3Fb%3D1%23c%252F%20d".encode()
    assert request.url.query == b""
    assert request.url.fragment == ""


async def test_client_options_block_redirects_and_environment(monkeypatch):
    captured = {}
    real_client = httpx2.AsyncClient

    def spy(**kwargs):
        captured.update(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(api_client.httpx2, "AsyncClient", spy)
    client, _ = make_client(respond(200, make_list()))
    await client.list_weighing_records({})

    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    timeout = captured["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (3.0, 10.0, 10.0, 3.0)


def test_only_approved_read_operations_are_public():
    public = {name for name in vars(FactoryApiClient) if not name.startswith("_")}
    assert public == {
        "list_weighing_records",
        "get_weighing_record_by_ticket",
        "get_weighing_record_by_id",
    }


# ---------------------------------------------------------------------------
# 응답 필드 허용 목록과 구조 검증
# ---------------------------------------------------------------------------


async def test_unknown_response_fields_are_dropped():
    item = make_record(vehicle_no_norm="12가3456", internal_note="비공개")
    client, _ = make_client(respond(200, make_list([item], debug_sql="SELECT 1")))

    result = await client.list_weighing_records({})

    dumped = result.model_dump()
    assert set(dumped) == {"items", "page", "page_size", "total", "total_pages"}
    assert set(dumped["items"][0]) == set(make_record())


async def test_list_items_never_carry_cancel_fields():
    item = make_detail(cancel_reason="TEST-reason", cancelled_by="TEST-op")
    client, _ = make_client(respond(200, make_list([item])))

    result = await client.list_weighing_records({})

    assert "cancel_reason" not in result.model_dump()["items"][0]


async def test_in_progress_record_with_null_weights_is_valid():
    detail = make_detail(
        status="IN_PROGRESS", tare_weight_kg=None, net_weight_kg=None, first_weighed_at=None
    )
    client, _ = make_client(respond(200, detail))

    result = await client.get_weighing_record_by_id(1)

    assert result.net_weight_kg is None
    assert result.first_weighed_at is None


def _without(body, key):
    return {k: v for k, v in body.items() if k != key}


@pytest.mark.parametrize(
    "body",
    [
        _without(make_detail(), "net_weight_kg"),
        _without(make_detail(), "cancel_reason"),
        _without(make_detail(), "ticket_no"),
        make_detail(id="1"),
        make_detail(id=True),
        make_detail(gross_weight_kg="15000"),
        make_detail(gross_weight_kg=15000.5),
        make_detail(direction="SIDEWAYS"),
        make_detail(status="DELETED"),
        make_detail(ticket_no=None),
        make_detail(created_at="2026-10-01T00:30:00"),
        make_detail(created_at="어제"),
        make_detail(cancelled_at="2026-10-01 00:30"),
        [make_detail()],
        "ok",
        None,
    ],
)
async def test_malformed_detail_response_is_an_error(body):
    client, _ = make_client(respond(200, body))
    with pytest.raises(ApiError) as excinfo:
        await client.get_weighing_record_by_id(1)
    assert excinfo.value.message == api_client.MSG_BAD_RESPONSE


@pytest.mark.parametrize(
    "body",
    [
        _without(make_list(), "total"),
        _without(make_list(), "items"),
        make_list(items={"id": 1}),
        make_list([_without(make_record(), "vehicle_no")]),
        make_list(total="1"),
        {"detail": "unexpected"},
    ],
)
async def test_malformed_list_response_is_an_error(body):
    client, _ = make_client(respond(200, body))
    with pytest.raises(ApiError) as excinfo:
        await client.list_weighing_records({})
    assert excinfo.value.message == api_client.MSG_BAD_RESPONSE


async def test_non_json_response_is_an_error():
    client, _ = make_client(lambda _r: httpx2.Response(200, text="<html>Traceback SELECT</html>"))
    with pytest.raises(ApiError) as excinfo:
        await client.list_weighing_records({})
    assert excinfo.value.message == api_client.MSG_BAD_RESPONSE


async def test_oversized_response_is_an_error(monkeypatch):
    monkeypatch.setattr(api_client, "MAX_RESPONSE_BYTES", 10)
    client, _ = make_client(respond(200, make_list()))
    with pytest.raises(ApiError) as excinfo:
        await client.list_weighing_records({})
    assert excinfo.value.message == api_client.MSG_BAD_RESPONSE


# ---------------------------------------------------------------------------
# 상태코드별 처리
# ---------------------------------------------------------------------------


async def test_404_uses_fixed_message():
    client, _ = make_client(respond(404, {"detail": "내부 메시지 SELECT"}))
    with pytest.raises(ApiError) as excinfo:
        await client.get_weighing_record_by_id(999)
    assert excinfo.value.message == api_client.MSG_NOT_FOUND


async def test_422_keeps_field_and_message_only():
    body = {
        "detail": [
            {
                "type": "value_error",
                "loc": ["query", "started_from"],
                "msg": "Value error, 타임존 정보가 없는 날짜는 허용하지 않습니다",
                "input": "SECRET-INPUT",
                "ctx": {"error": "SECRET-CTX"},
                "url": "https://errors.pydantic.dev/x",
            },
            {"type": "x", "loc": ["query"], "msg": "started_from은 started_to보다 빨라야 합니다"},
        ]
    }
    client, _ = make_client(respond(422, body))

    with pytest.raises(ApiError) as excinfo:
        await client.list_weighing_records({"started_from": "2026-10-01T00:00:00"})

    message = excinfo.value.message
    assert message.startswith(api_client.MSG_INVALID_INPUT)
    assert "started_from: Value error, 타임존 정보가 없는 날짜는 허용하지 않습니다" in message
    assert "started_from은 started_to보다 빨라야 합니다" in message
    for hidden in ("SECRET-INPUT", "SECRET-CTX", "errors.pydantic.dev", "query"):
        assert hidden not in message


@pytest.mark.parametrize("body", [{"detail": "문자열 상세"}, {"detail": [{"loc": ["query"]}]}, {}, ["x"]])
async def test_422_with_unexpected_shape_falls_back_to_generic_message(body):
    client, _ = make_client(respond(422, body))
    with pytest.raises(ApiError) as excinfo:
        await client.list_weighing_records({})
    assert excinfo.value.message == api_client.MSG_INVALID_INPUT


async def test_422_message_is_bounded():
    detail = [{"loc": ["query", f"f{i}"], "msg": "m" * 5000} for i in range(50)]
    client, _ = make_client(respond(422, {"detail": detail}))
    with pytest.raises(ApiError) as excinfo:
        await client.list_weighing_records({})
    assert len(excinfo.value.message) < 3000


@pytest.mark.parametrize("status", [400, 401, 403, 405, 500, 502, 503])
async def test_other_statuses_hide_the_body(status):
    leak = {"detail": "Traceback psycopg SELECT * FROM weighing_records fakeuser:fakepass@db"}
    client, _ = make_client(respond(status, leak))

    with pytest.raises(ApiError) as excinfo:
        await client.get_weighing_record_by_id(1)

    assert excinfo.value.message == f"{api_client.MSG_REQUEST_FAILED} (HTTP {status})"
    assert_no_internal_details(excinfo.value.message)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirect_is_not_followed(status):
    def handler(request):
        if request.url.path == "/canary":
            return httpx2.Response(200, json=make_detail(ticket_no="REDIRECTED"))
        return httpx2.Response(status, headers={"Location": "http://127.0.0.1:8000/canary"})

    client, seen = make_client(handler)

    with pytest.raises(ApiError) as excinfo:
        await client.get_weighing_record_by_id(1)

    assert excinfo.value.message == f"{api_client.MSG_REQUEST_FAILED} (HTTP {status})"
    assert [r.url.path for r in seen] == [f"{RECORDS_PATH}/1"]


# ---------------------------------------------------------------------------
# 연결 실패와 타임아웃
# ---------------------------------------------------------------------------


def _raising(exc_type):
    def handler(request):
        raise exc_type("postgresql://fakeuser:fakepass@db internal detail", request=request)

    return handler


@pytest.mark.parametrize(
    ("exc_type", "expected"),
    [
        (httpx2.ConnectError, api_client.MSG_CONNECT),
        (httpx2.ConnectTimeout, api_client.MSG_CONNECT),
        (httpx2.ReadTimeout, api_client.MSG_TIMEOUT),
        (httpx2.WriteTimeout, api_client.MSG_TIMEOUT),
        (httpx2.PoolTimeout, api_client.MSG_TIMEOUT),
        (httpx2.ReadError, api_client.MSG_REQUEST_FAILED),
        (httpx2.RemoteProtocolError, api_client.MSG_REQUEST_FAILED),
    ],
)
async def test_transport_failures_use_fixed_messages_and_do_not_retry(exc_type, expected):
    client, seen = make_client(_raising(exc_type))

    with pytest.raises(ApiError) as excinfo:
        await client.list_weighing_records({})

    assert excinfo.value.message == expected
    assert excinfo.value.__cause__ is None
    assert len(seen) == 1


async def test_http_client_is_closed_after_success_and_failure(monkeypatch):
    created = []
    real_client = httpx2.AsyncClient

    def tracking(**kwargs):
        client = real_client(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(api_client.httpx2, "AsyncClient", tracking)

    ok, _ = make_client(respond(200, make_list()))
    await ok.list_weighing_records({})
    failing, _ = make_client(_raising(httpx2.ConnectError))
    with pytest.raises(ApiError):
        await failing.list_weighing_records({})

    assert len(created) == 2
    assert all(client.is_closed for client in created)
