"""도구 정의와 입력 검증(프로토콜 없이 call_tool을 직접 호출)."""

import httpx2
import pytest

from factory_api_mcp import server
from factory_api_mcp.api_client import MSG_INVALID_INPUT, MSG_NOT_FOUND, FactoryApiClient
from factory_api_mcp.server import TOOLS, call_tool
from tests.conftest import (
    RECORDS_PATH,
    assert_no_internal_details,
    make_detail,
    make_list,
    result_text,
)

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = ["list_weighing_records", "get_weighing_record_by_ticket", "get_weighing_record_by_id"]


def make_client(handler=None):
    seen = []

    def default_handler(request):
        if request.url.path == RECORDS_PATH:
            return httpx2.Response(200, json=make_list())
        return httpx2.Response(200, json=make_detail())

    def recording_handler(request):
        seen.append(request)
        return (handler or default_handler)(request)

    client = FactoryApiClient("http://127.0.0.1:8000", transport=httpx2.MockTransport(recording_handler))
    return client, seen


# ---------------------------------------------------------------------------
# 도구 정의
# ---------------------------------------------------------------------------


def test_exactly_three_approved_tools():
    assert [spec.name for spec in TOOLS] == EXPECTED_TOOLS


@pytest.mark.parametrize("spec", TOOLS, ids=lambda spec: spec.name)
def test_tool_definition_is_closed_and_read_only(spec):
    tool = server._tool_definition(spec).model_dump(by_alias=True, exclude_none=True)

    assert tool["inputSchema"]["type"] == "object"
    assert tool["inputSchema"]["additionalProperties"] is False
    assert tool["annotations"] == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert tool["outputSchema"]["type"] == "object"


def test_no_tool_accepts_a_url_host_or_path_argument():
    forbidden = {"url", "base_url", "host", "hostname", "port", "path", "endpoint", "method", "headers"}
    for spec in TOOLS:
        properties = set(spec.args_model.model_json_schema()["properties"])
        assert not properties & forbidden


def test_list_arguments_match_api_query_parameters():
    schema = TOOLS[0].args_model.model_json_schema()
    assert set(schema["properties"]) == {
        "direction",
        "status",
        "vehicle_number",
        "partner_name",
        "material_name",
        "ticket_no",
        "started_from",
        "started_to",
        "page",
        "page_size",
        "sort_dir",
    }
    assert "required" not in schema


# ---------------------------------------------------------------------------
# 정상 호출
# ---------------------------------------------------------------------------


async def test_list_passes_filters_through_unchanged():
    client, seen = make_client()
    arguments = {
        "direction": "OUTBOUND",
        "status": "CANCELED",
        "vehicle_number": "12가-3456",
        "partner_name": "거래처",
        "material_name": "품목",
        "ticket_no": "20261001-0001",
        "started_from": "2026-10-01T00:00:00+09:00",
        "started_to": "2026-10-02T00:00:00+09:00",
        "page": 3,
        "page_size": 100,
        "sort_dir": "asc",
    }

    result = await call_tool(client, "list_weighing_records", arguments)

    assert result.is_error is False
    assert dict(seen[0].url.params) == {k: str(v) for k, v in arguments.items()}
    assert result.structured_content == make_list()


async def test_list_omits_unset_and_null_arguments():
    client, seen = make_client()
    await call_tool(client, "list_weighing_records", {"direction": None, "page": 2})
    assert dict(seen[0].url.params) == {"page": "2"}


async def test_list_accepts_missing_arguments():
    client, seen = make_client()
    result = await call_tool(client, "list_weighing_records", None)
    assert result.is_error is False
    assert seen[0].url.query == b""


async def test_range_rules_are_left_to_the_api():
    """page=0, page_size=101 같은 값은 여기서 막지 않고 API의 422에 맡긴다."""

    client, seen = make_client()
    await call_tool(client, "list_weighing_records", {"page": 0, "page_size": 101})
    assert dict(seen[0].url.params) == {"page": "0", "page_size": "101"}


async def test_detail_results_include_cancel_fields():
    detail = make_detail(
        status="CANCELED",
        cancelled_at="2026-10-01T02:00:00+00:00",
        cancelled_by="TEST-op",
        cancel_reason="TEST-reason",
    )
    client, seen = make_client(lambda _r: httpx2.Response(200, json=detail))

    by_id = await call_tool(client, "get_weighing_record_by_id", {"record_id": 7})
    by_ticket = await call_tool(client, "get_weighing_record_by_ticket", {"ticket_no": "20261001-0001"})

    assert by_id.structured_content == detail
    assert by_ticket.structured_content == detail
    assert [r.url.path for r in seen] == [f"{RECORDS_PATH}/7", f"{RECORDS_PATH}/ticket/20261001-0001"]


async def test_text_content_mirrors_structured_content():
    import json

    client, _ = make_client()
    result = await call_tool(client, "get_weighing_record_by_id", {"record_id": 1})
    assert json.loads(result_text(result)) == result.structured_content


# ---------------------------------------------------------------------------
# 입력 검증: API를 호출하기 전에 거부
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("list_weighing_records", {"url": "http://example.com"}),
        ("list_weighing_records", {"base_url": "http://example.com"}),
        ("list_weighing_records", {"host": "example.com"}),
        ("list_weighing_records", {"path": "/health/db"}),
        ("list_weighing_records", {"sort_by": "id"}),
        ("list_weighing_records", {"direction": "SIDEWAYS"}),
        ("list_weighing_records", {"status": "DELETED"}),
        ("list_weighing_records", {"sort_dir": "up"}),
        ("list_weighing_records", {"page": "2"}),
        ("list_weighing_records", {"page": 1.5}),
        ("list_weighing_records", {"page": True}),
        ("list_weighing_records", {"partner_name": 123}),
        ("get_weighing_record_by_id", {}),
        ("get_weighing_record_by_id", {"record_id": 0}),
        ("get_weighing_record_by_id", {"record_id": -1}),
        ("get_weighing_record_by_id", {"record_id": "1"}),
        ("get_weighing_record_by_id", {"record_id": "1/../../health"}),
        ("get_weighing_record_by_id", {"record_id": 1.0}),
        ("get_weighing_record_by_id", {"record_id": 2**63}),
        ("get_weighing_record_by_id", {"record_id": 1, "url": "http://example.com"}),
        ("get_weighing_record_by_ticket", {}),
        ("get_weighing_record_by_ticket", {"ticket_no": ""}),
        ("get_weighing_record_by_ticket", {"ticket_no": "   "}),
        ("get_weighing_record_by_ticket", {"ticket_no": "."}),
        ("get_weighing_record_by_ticket", {"ticket_no": ".."}),
        ("get_weighing_record_by_ticket", {"ticket_no": "../1"}),
        ("get_weighing_record_by_ticket", {"ticket_no": "a/b"}),
        ("get_weighing_record_by_ticket", {"ticket_no": "a\\b"}),
        ("get_weighing_record_by_ticket", {"ticket_no": "a\nb"}),
        ("get_weighing_record_by_ticket", {"ticket_no": "x" * 21}),
        ("get_weighing_record_by_ticket", {"ticket_no": 20261001}),
        ("get_weighing_record_by_ticket", {"ticket_no": "T", "path": "/health"}),
    ],
)
async def test_invalid_arguments_are_rejected_without_calling_the_api(name, arguments):
    client, seen = make_client()

    result = await call_tool(client, name, arguments)

    assert result.is_error is True
    assert result.structured_content is None
    assert result_text(result).startswith(MSG_INVALID_INPUT)
    assert seen == []


async def test_argument_error_does_not_echo_input_or_doc_urls():
    client, _ = make_client()
    result = await call_tool(client, "list_weighing_records", {"direction": "SECRET-VALUE"})
    text = result_text(result)
    assert "direction" in text
    assert "SECRET-VALUE" not in text
    assert "errors.pydantic.dev" not in text


@pytest.mark.parametrize("name", ["", "delete_weighing_record", "factory-api__list_weighing_records", "exec"])
async def test_unknown_tool_is_an_error(name):
    client, seen = make_client()
    result = await call_tool(client, name, {})
    assert result.is_error is True
    assert result_text(result) == server.MSG_UNKNOWN_TOOL
    assert seen == []


# ---------------------------------------------------------------------------
# 오류 결과
# ---------------------------------------------------------------------------


async def test_not_found_is_an_error_result():
    client, _ = make_client(lambda _r: httpx2.Response(404, json={"detail": "x"}))
    result = await call_tool(client, "get_weighing_record_by_ticket", {"ticket_no": "NOPE"})
    assert result.is_error is True
    assert result_text(result) == MSG_NOT_FOUND
    assert result.structured_content is None


async def test_unexpected_exception_is_not_exposed():
    def handler(_request):
        raise RuntimeError("Traceback SELECT secret FROM weighing_records fakeuser:fakepass@db")

    client, _ = make_client(handler)

    result = await call_tool(client, "get_weighing_record_by_id", {"record_id": 1})

    assert result.is_error is True
    assert result_text(result) == server.MSG_INTERNAL
    assert_no_internal_details(result_text(result))
