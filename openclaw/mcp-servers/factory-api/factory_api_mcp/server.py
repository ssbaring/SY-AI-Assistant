"""MCP 서버 정의: 계근기록 조회 도구 3개.

SDK의 저수준 Server를 쓴다. 입력 스키마(정의되지 않은 인자 거부)와 오류 문구를
직접 통제하기 위해서다. 도구 결과에는 고정 문구만 넣고, 예외 내용·스택트레이스·
API 주소는 넣지 않는다.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx2
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ValidationError

from factory_api_mcp import __version__
from factory_api_mcp.api_client import MSG_INVALID_INPUT, ApiError, FactoryApiClient
from factory_api_mcp.schemas import (
    GetWeighingRecordByIdArgs,
    GetWeighingRecordByTicketArgs,
    ListWeighingRecordsArgs,
    WeighingRecordDetail,
    WeighingRecordList,
)

logger = logging.getLogger(__name__)

SERVER_NAME = "factory-api"

MSG_UNKNOWN_TOOL = "알 수 없는 도구입니다."
MSG_INTERNAL = "도구 실행 중 오류가 발생했습니다."

_INSTRUCTIONS = (
    "공장 계근기록을 조회하는 읽기 전용 도구다. 중량 단위는 kg 정수이고 시각은 UTC다. "
    "조회되지 않은 값은 만들어내지 않는다."
)

_READ_ONLY = types.ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass(frozen=True)
class _ToolSpec:
    name: str
    title: str
    description: str
    args_model: type[BaseModel]
    result_model: type[BaseModel]
    run: Callable[[FactoryApiClient, Any], Awaitable[BaseModel]]


async def _list(client: FactoryApiClient, args: ListWeighingRecordsArgs) -> BaseModel:
    # 지정하지 않은 값은 보내지 않는다. 기본값과 범위 검증은 API가 맡는다.
    return await client.list_weighing_records(args.model_dump(exclude_none=True))


async def _by_ticket(client: FactoryApiClient, args: GetWeighingRecordByTicketArgs) -> BaseModel:
    return await client.get_weighing_record_by_ticket(args.ticket_no)


async def _by_id(client: FactoryApiClient, args: GetWeighingRecordByIdArgs) -> BaseModel:
    return await client.get_weighing_record_by_id(args.record_id)


TOOLS: tuple[_ToolSpec, ...] = (
    _ToolSpec(
        name="list_weighing_records",
        title="계근기록 목록 조회",
        description=(
            "계근기록 목록을 조회한다. 조건은 모두 선택이며 함께 쓰면 AND로 적용된다. "
            "날짜 범위는 started_from <= 첫 계근 시각 < started_to 이고 타임존이 있는 값만 받는다. "
            "결과는 items, page, page_size, total, total_pages이며 취소 사유 등은 상세 조회에만 있다. "
            "total이 page_size보다 크면 page를 올려 나머지를 조회한다."
        ),
        args_model=ListWeighingRecordsArgs,
        result_model=WeighingRecordList,
        run=_list,
    ),
    _ToolSpec(
        name="get_weighing_record_by_ticket",
        title="계근번호로 상세 조회",
        description="계근번호(ticket_no)가 정확히 일치하는 계근기록 1건을 취소 정보와 함께 조회한다.",
        args_model=GetWeighingRecordByTicketArgs,
        result_model=WeighingRecordDetail,
        run=_by_ticket,
    ),
    _ToolSpec(
        name="get_weighing_record_by_id",
        title="ID로 상세 조회",
        description="계근기록 ID(정수)로 1건을 취소 정보와 함께 조회한다.",
        args_model=GetWeighingRecordByIdArgs,
        result_model=WeighingRecordDetail,
        run=_by_id,
    ),
)

_TOOLS_BY_NAME = {spec.name: spec for spec in TOOLS}


def _tool_definition(spec: _ToolSpec) -> types.Tool:
    return types.Tool(
        name=spec.name,
        title=spec.title,
        description=spec.description,
        input_schema=spec.args_model.model_json_schema(),
        output_schema=spec.result_model.model_json_schema(),
        annotations=_READ_ONLY,
    )


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


def _describe_argument_errors(exc: ValidationError) -> str:
    # 입력값 자체(input)와 pydantic 문서 URL은 넣지 않는다.
    lines = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False)[:10]:
        field = ".".join(str(part) for part in error["loc"])
        lines.append(f"{field}: {error['msg']}" if field else error["msg"])
    return f"{MSG_INVALID_INPUT} " + "; ".join(lines)


async def call_tool(
    client: FactoryApiClient, name: str, arguments: dict[str, Any] | None
) -> types.CallToolResult:
    spec = _TOOLS_BY_NAME.get(name)
    if spec is None:
        return _error(MSG_UNKNOWN_TOOL)

    try:
        args = spec.args_model.model_validate(arguments or {})
    except ValidationError as exc:
        return _error(_describe_argument_errors(exc))

    try:
        result = await spec.run(client, args)
    except ApiError as exc:
        return _error(exc.message)
    except Exception as exc:  # 예상하지 못한 오류의 내용은 밖으로 내보내지 않는다.
        logger.error("도구 %s 실행 중 예상하지 못한 오류: %s", name, type(exc).__name__)
        return _error(MSG_INTERNAL)

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=result.model_dump_json())],
        structured_content=result.model_dump(mode="json"),
        is_error=False,
    )


def build_server(base_url: str, *, transport: httpx2.AsyncBaseTransport | None = None) -> Server[Any]:
    client = FactoryApiClient(base_url, transport=transport)

    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[_tool_definition(spec) for spec in TOOLS])

    async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await call_tool(client, params.name, params.arguments)

    return Server(
        SERVER_NAME,
        version=__version__,
        instructions=_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def run_stdio(base_url: str) -> None:
    server = build_server(base_url)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
