"""계근기록 조회 API 호출.

- GET만 보낸다. 공개 메서드는 승인된 조회 3개뿐이고 경로는 코드에 고정되어 있다.
- 리다이렉트를 따라가지 않는다(3xx는 오류).
- 프록시 등 환경변수 설정을 쓰지 않는다(trust_env=False).
- 자동 재시도를 하지 않는다.
- 요청을 보내고 응답 본문을 다 읽을 때까지의 전체 시간에 상한을 둔다.
- 응답 본문은 조금씩 읽으면서 크기를 세고, 상한을 넘으면 읽기를 중단한다.
- 실패 원인은 stderr 로그에만 남기고, 호출자에게는 고정 문구(ApiError)만 준다.
"""

import json
import logging
from collections.abc import Mapping
from typing import TypeVar
from urllib.parse import quote

import anyio
import httpx2
from pydantic import BaseModel, ValidationError

from factory_api_mcp.schemas import WeighingRecordDetail, WeighingRecordList

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 10.0
WRITE_TIMEOUT_SECONDS = 10.0
POOL_TIMEOUT_SECONDS = 3.0
# 연결부터 응답 본문을 다 읽을 때까지의 전체 상한. read 타임아웃은 "다음 조각"을
# 기다리는 시간일 뿐이라, 조금씩 계속 오는 응답은 이 상한이 없으면 끝없이 이어진다.
TOTAL_TIMEOUT_SECONDS = 15.0

MAX_RESPONSE_BYTES = 5 * 1024 * 1024

_RECORDS_PATH = "/api/v1/weighing-records"

_MAX_VALIDATION_ITEMS = 10
_MAX_VALIDATION_TEXT = 200

MSG_NOT_FOUND = "해당 계근기록을 찾을 수 없습니다."
MSG_CONNECT = "API 서버에 연결할 수 없습니다."
MSG_TIMEOUT = "API 응답 시간이 초과되었습니다."
MSG_REQUEST_FAILED = "API 요청을 처리하지 못했습니다."
MSG_BAD_RESPONSE = "API 응답 형식이 올바르지 않습니다."
MSG_INVALID_INPUT = "입력값이 올바르지 않습니다."

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ApiError(Exception):
    """도구 결과에 그대로 내보내도 되는 문구만 담는다."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _ResponseTooLarge(Exception):
    pass


def _describe_validation_errors(content: bytes) -> str:
    """API 422 응답에서 필드 위치와 메시지만 꺼낸다(input, ctx, url 등은 버린다)."""

    try:
        body = json.loads(content)
    except ValueError:
        return MSG_INVALID_INPUT

    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, list):
        return MSG_INVALID_INPUT

    lines: list[str] = []
    for item in detail[:_MAX_VALIDATION_ITEMS]:
        if not isinstance(item, dict) or not isinstance(item.get("msg"), str):
            continue
        loc = item.get("loc")
        names = (
            [str(part) for part in loc if isinstance(part, (str, int)) and part not in ("query", "path")]
            if isinstance(loc, list)
            else []
        )
        message = item["msg"][:_MAX_VALIDATION_TEXT]
        field = ".".join(names)[:_MAX_VALIDATION_TEXT]
        lines.append(f"{field}: {message}" if field else message)

    if not lines:
        return MSG_INVALID_INPUT
    return f"{MSG_INVALID_INPUT} " + "; ".join(lines)


class FactoryApiClient:
    def __init__(self, base_url: str, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url
        self._transport = transport

    async def list_weighing_records(self, params: Mapping[str, str | int]) -> WeighingRecordList:
        return await self._get(_RECORDS_PATH, WeighingRecordList, params=params)

    async def get_weighing_record_by_ticket(self, ticket_no: str) -> WeighingRecordDetail:
        return await self._get(f"{_RECORDS_PATH}/ticket/{quote(ticket_no, safe='')}", WeighingRecordDetail)

    async def get_weighing_record_by_id(self, record_id: int) -> WeighingRecordDetail:
        return await self._get(f"{_RECORDS_PATH}/{int(record_id)}", WeighingRecordDetail)

    async def _get(
        self,
        path: str,
        model: type[_ModelT],
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> _ModelT:
        try:
            with anyio.fail_after(TOTAL_TIMEOUT_SECONDS):
                status, content = await self._fetch(path, params)
        except TimeoutError:
            logger.warning("API 전체 시간 상한(%.0f초) 초과", TOTAL_TIMEOUT_SECONDS)
            raise ApiError(MSG_TIMEOUT) from None
        except (httpx2.ConnectError, httpx2.ConnectTimeout) as exc:
            logger.warning("API 연결 실패: %s", type(exc).__name__)
            raise ApiError(MSG_CONNECT) from None
        except httpx2.TimeoutException as exc:
            logger.warning("API 시간 초과: %s", type(exc).__name__)
            raise ApiError(MSG_TIMEOUT) from None
        except httpx2.HTTPError as exc:
            logger.warning("API 요청 실패: %s", type(exc).__name__)
            raise ApiError(MSG_REQUEST_FAILED) from None
        except _ResponseTooLarge:
            logger.warning("API 응답이 상한(%d bytes)을 넘어 읽기를 중단", MAX_RESPONSE_BYTES)
            raise ApiError(MSG_BAD_RESPONSE) from None

        if status == 404:
            raise ApiError(MSG_NOT_FOUND)
        if status == 422:
            raise ApiError(_describe_validation_errors(content))
        if status != 200:
            # 3xx(리다이렉트)도 여기로 온다. 응답 본문은 읽지 않았다.
            logger.warning("API가 예상하지 못한 상태코드를 반환: %s", status)
            raise ApiError(f"{MSG_REQUEST_FAILED} (HTTP {status})")

        try:
            # JSON이 아니거나, 필수 필드가 없거나, 타입이 다르면 모두 여기서 걸린다.
            return model.model_validate_json(content)
        except ValidationError as exc:
            logger.warning("API 응답 구조 불일치: 오류 %d건", exc.error_count())
            raise ApiError(MSG_BAD_RESPONSE) from None

    async def _fetch(self, path: str, params: Mapping[str, str | int] | None) -> tuple[int, bytes]:
        """(상태코드, 본문). 본문은 200과 422일 때만 읽는다.

        호출마다 클라이언트를 만들고 닫는다. 정상 종료, 오류, 시간 초과, 호출 취소
        어느 경우에도 두 async with 블록을 벗어나면서 응답 스트림과 연결이 닫힌다.
        """

        timeout = httpx2.Timeout(
            connect=CONNECT_TIMEOUT_SECONDS,
            read=READ_TIMEOUT_SECONDS,
            write=WRITE_TIMEOUT_SECONDS,
            pool=POOL_TIMEOUT_SECONDS,
        )
        async with httpx2.AsyncClient(
            base_url=self._base_url,
            transport=self._transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            # 압축을 받지 않는다. 받은 바이트 수와 본문 크기가 같아진다.
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as client:
            async with client.stream("GET", path, params=params) as response:
                status = response.status_code
                if status not in (200, 422):
                    return status, b""

                # Content-Length를 믿지 않고 실제로 받은 양을 센다. 서버가 압축을
                # 보내더라도 풀린 뒤의 크기를 세므로 메모리 사용량은 상한을 넘지 않는다.
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise _ResponseTooLarge
                return status, bytes(body)
