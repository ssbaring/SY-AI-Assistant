"""실제 stdio MCP 프로토콜 테스트.

MCP 서버를 자식 프로세스로 띄우고 SDK 클라이언트로 initialize, tools/list,
tools/call을 주고받는다. API는 임시 포트의 가짜 HTTP 서버가 대신한다(DB 없음).
"""

import json
import os
import socket
import subprocess
import sys

import mcp_types as types
import pytest

from factory_api_mcp import __version__
from factory_api_mcp.api_client import (
    MSG_BAD_RESPONSE,
    MSG_CONNECT,
    MSG_INVALID_INPUT,
    MSG_NOT_FOUND,
    MSG_REQUEST_FAILED,
)
from factory_api_mcp.server import MSG_UNKNOWN_TOOL
from tests.conftest import (
    PROJECT_ROOT,
    RECORDS_PATH,
    assert_no_internal_details,
    make_detail,
    make_list,
    mcp_session,
    result_text,
)

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = ["list_weighing_records", "get_weighing_record_by_ticket", "get_weighing_record_by_id"]


def _child_env(**overrides):
    env = {k: v for k, v in os.environ.items() if k != "FACTORY_API_BASE_URL"}
    env.update(overrides)
    return env


def _unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# initialize, tools/list
# ---------------------------------------------------------------------------


async def test_initialize_reports_server_identity(stub_api):
    async with mcp_session(stub_api.base_url) as session:
        result = await session.initialize()

    assert result.server_info.name == "factory-api"
    assert result.server_info.version == __version__
    assert result.capabilities.tools is not None
    assert result.capabilities.resources is None
    assert result.capabilities.prompts is None
    assert stub_api.requests == []  # 연결만으로는 API를 호출하지 않는다.


async def test_tools_list_returns_only_the_three_read_only_tools(stub_api):
    async with mcp_session(stub_api.base_url) as session:
        await session.initialize()
        listed = await session.list_tools()

    assert [tool.name for tool in listed.tools] == EXPECTED_TOOLS
    for tool in listed.tools:
        assert tool.input_schema["additionalProperties"] is False
        assert tool.output_schema is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.description


# ---------------------------------------------------------------------------
# tools/call: 정상
# ---------------------------------------------------------------------------


async def test_call_all_three_tools(stub_api):
    async with mcp_session(stub_api.base_url) as session:
        await session.initialize()
        listed = await session.call_tool(
            "list_weighing_records",
            {"direction": "INBOUND", "started_from": "2026-10-01T00:00:00+09:00", "page_size": 5},
        )
        by_ticket = await session.call_tool("get_weighing_record_by_ticket", {"ticket_no": "20261001-0001"})
        by_id = await session.call_tool("get_weighing_record_by_id", {"record_id": 1})

    assert listed.is_error is False
    assert listed.structured_content == make_list()
    assert json.loads(result_text(listed)) == make_list()
    assert by_ticket.is_error is False
    assert by_ticket.structured_content == make_detail()
    assert by_id.is_error is False
    assert by_id.structured_content == make_detail()

    assert stub_api.requests == [
        (
            "GET",
            RECORDS_PATH,
            {"direction": ["INBOUND"], "started_from": ["2026-10-01T00:00:00+09:00"], "page_size": ["5"]},
        ),
        ("GET", f"{RECORDS_PATH}/ticket/20261001-0001", {}),
        ("GET", f"{RECORDS_PATH}/1", {}),
    ]


# ---------------------------------------------------------------------------
# tools/call: 오류도 프로토콜 수준에서 isError 결과로 온다
# ---------------------------------------------------------------------------


async def test_error_results_over_the_protocol(stub_api):
    async with mcp_session(stub_api.base_url) as session:
        await session.initialize()
        not_found_id = await session.call_tool("get_weighing_record_by_id", {"record_id": 404})
        not_found_ticket = await session.call_tool("get_weighing_record_by_ticket", {"ticket_no": "NOPE"})
        invalid_by_api = await session.call_tool("list_weighing_records", {"page": 0})
        server_error = await session.call_tool("get_weighing_record_by_id", {"record_id": 500})
        redirect = await session.call_tool("get_weighing_record_by_id", {"record_id": 302})
        broken = await session.call_tool("get_weighing_record_by_id", {"record_id": 777})
        extra_argument = await session.call_tool("list_weighing_records", {"url": "http://example.com"})
        bad_path = await session.call_tool("get_weighing_record_by_ticket", {"ticket_no": "../1"})
        unknown = await session.call_tool("delete_weighing_record", {"record_id": 1})
        # 오류 뒤에도 같은 연결에서 정상 호출이 된다.
        still_works = await session.call_tool("get_weighing_record_by_id", {"record_id": 1})

    results = {
        "not_found_id": (not_found_id, MSG_NOT_FOUND),
        "not_found_ticket": (not_found_ticket, MSG_NOT_FOUND),
        "invalid_by_api": (invalid_by_api, f"{MSG_INVALID_INPUT} page: Input should be greater than or equal to 1"),
        "server_error": (server_error, f"{MSG_REQUEST_FAILED} (HTTP 500)"),
        "redirect": (redirect, f"{MSG_REQUEST_FAILED} (HTTP 302)"),
        "broken": (broken, MSG_BAD_RESPONSE),
        "unknown": (unknown, MSG_UNKNOWN_TOOL),
    }
    for label, (result, expected_text) in results.items():
        assert result.is_error is True, label
        assert result.structured_content is None, label
        assert result_text(result) == expected_text, label
        assert_no_internal_details(result_text(result))

    for result in (extra_argument, bad_path):
        assert result.is_error is True
        assert result_text(result).startswith(MSG_INVALID_INPUT)

    assert still_works.is_error is False

    paths = [path for _method, path, _query in stub_api.requests]
    assert "/canary" not in paths  # 리다이렉트를 따라가지 않았다.
    assert all(method == "GET" for method, _path, _query in stub_api.requests)
    # 입력 검증에서 걸린 호출과 없는 도구 호출은 API에 도달하지 않는다.
    assert paths == [
        f"{RECORDS_PATH}/404",
        f"{RECORDS_PATH}/ticket/NOPE",
        RECORDS_PATH,
        f"{RECORDS_PATH}/500",
        f"{RECORDS_PATH}/302",
        f"{RECORDS_PATH}/777",
        f"{RECORDS_PATH}/1",
    ]


async def test_connection_failure_is_an_error_result():
    base_url = f"http://127.0.0.1:{_unused_port()}"  # 아무도 듣고 있지 않은 포트

    async with mcp_session(base_url) as session:
        await session.initialize()
        result = await session.call_tool("list_weighing_records", {})

    assert result.is_error is True
    assert result_text(result) == MSG_CONNECT
    assert_no_internal_details(result_text(result))


# ---------------------------------------------------------------------------
# 시작 거부, stdout 순수성
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_overrides",
    [
        {},
        {"FACTORY_API_BASE_URL": "http://example.com:8000"},
        {"FACTORY_API_BASE_URL": "http://user:s3cret@127.0.0.1:8000"},
        {"FACTORY_API_BASE_URL": "http://127.0.0.1:8000/api"},
    ],
)
def test_refuses_to_start_without_a_valid_base_url(env_overrides):
    completed = subprocess.run(
        [sys.executable, "-m", "factory_api_mcp"],
        cwd=PROJECT_ROOT,
        env=_child_env(**env_overrides),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
    )

    stderr = completed.stderr.decode("utf-8")
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert "FACTORY_API_BASE_URL" in stderr
    assert "s3cret" not in stderr
    assert "Traceback" not in stderr


def test_stdout_carries_only_json_rpc_messages(stub_api):
    """SDK 클라이언트 없이 원시 JSON-RPC를 보내고, stdout의 모든 줄을 검사한다."""

    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "raw-test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_weighing_record_by_id", "arguments": {"record_id": 1}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_weighing_record_by_id", "arguments": {"record_id": 500}},
        },
    ]
    payload = "".join(json.dumps(message) + "\n" for message in messages).encode("utf-8")

    process = subprocess.Popen(
        [sys.executable, "-m", "factory_api_mcp"],
        cwd=PROJECT_ROOT,
        env=_child_env(FACTORY_API_BASE_URL=stub_api.base_url),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.stdin.write(payload)
        process.stdin.flush()
        responses = {}
        lines = []
        while len(responses) < 4:
            line = process.stdout.readline()
            assert line, "응답을 다 받기 전에 서버 stdout이 닫혔다"
            lines.append(line)
            message = json.loads(line)
            if "id" in message:
                responses[message["id"]] = message
        # stdin을 닫으면 서버가 스스로 종료해야 한다.
        stdout_rest, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 0
    for line in lines + stdout_rest.splitlines():
        assert json.loads(line)["jsonrpc"] == "2.0"

    assert responses[1]["result"]["serverInfo"]["name"] == "factory-api"
    assert responses[1]["result"]["protocolVersion"] in types.version.SUPPORTED_PROTOCOL_VERSIONS
    assert [tool["name"] for tool in responses[2]["result"]["tools"]] == EXPECTED_TOOLS
    assert responses[3]["result"]["isError"] is False
    assert responses[3]["result"]["structuredContent"] == make_detail()
    assert responses[4]["result"]["isError"] is True
    assert responses[4]["result"]["content"] == [{"type": "text", "text": f"{MSG_REQUEST_FAILED} (HTTP 500)"}]

    # 로그는 stderr에만 있고, 그 안에도 API 응답 본문은 없다.
    log = stderr.decode("utf-8")
    assert "500" in log
    assert_no_internal_details(log.replace(stub_api.base_url, ""))
