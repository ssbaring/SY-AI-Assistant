"""도구 입력과 API 응답 스키마.

입력: 타입과 열거값만 확인한다. 필터·날짜·페이지 규칙은 API가 판단하고(422),
여기서 다시 구현하지 않는다. 정의되지 않은 인자는 거부한다.

응답: 문서화된 필드만 통과시키고(그 밖의 필드는 버림), 필수 필드가 없거나
타입이 다르면 오류로 처리한다.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Direction = Literal["INBOUND", "OUTBOUND"]
Status = Literal["IN_PROGRESS", "COMPLETED", "CANCELED"]
SortDir = Literal["asc", "desc"]

# weighing_records.ticket_no 컬럼 길이(String(20))와 같다.
TICKET_NO_MAX_LENGTH = 20
_BIGINT_MAX = 9_223_372_036_854_775_807


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListWeighingRecordsArgs(_Args):
    direction: Annotated[Direction | None, Field(description="입출고 구분")] = None
    status: Annotated[Status | None, Field(description="상태. CANCELED(취소)도 기본 목록에 포함된다")] = None
    vehicle_number: Annotated[
        str | None, Field(description="차량번호 완전일치(공백·하이픈·대소문자는 무시)")
    ] = None
    partner_name: Annotated[str | None, Field(description="거래처명 부분일치")] = None
    material_name: Annotated[str | None, Field(description="품목명 부분일치")] = None
    ticket_no: Annotated[str | None, Field(description="계근번호 완전일치")] = None
    started_from: Annotated[
        str | None,
        Field(description="첫 계근 시각 시작(포함). 타임존 필수 ISO 8601, 예: 2026-10-01T00:00:00+09:00"),
    ] = None
    started_to: Annotated[
        str | None,
        Field(description="첫 계근 시각 끝(미포함). 타임존 필수 ISO 8601, started_from보다 늦어야 한다"),
    ] = None
    page: Annotated[int | None, Field(description="페이지 번호(기본 1)")] = None
    page_size: Annotated[int | None, Field(description="페이지 크기(기본 20, 최대 100)")] = None
    sort_dir: Annotated[SortDir | None, Field(description="첫 계근 시각 정렬 방향(기본 desc)")] = None


class GetWeighingRecordByTicketArgs(_Args):
    ticket_no: Annotated[
        str, Field(min_length=1, max_length=TICKET_NO_MAX_LENGTH, description="계근번호")
    ]

    @field_validator("ticket_no")
    @classmethod
    def _safe_path_segment(cls, value: str) -> str:
        # 이 값은 URL 경로의 한 칸으로 들어간다. 다른 경로로 벗어날 수 있는 값은 막는다.
        if not value.strip():
            raise ValueError("계근번호가 비어 있습니다")
        if value in (".", ".."):
            raise ValueError("계근번호로 사용할 수 없는 값입니다")
        if any(ch in "/\\" or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError("계근번호에 /, \\ 또는 제어문자를 사용할 수 없습니다")
        return value


class GetWeighingRecordByIdArgs(_Args):
    record_id: Annotated[int, Field(ge=1, le=_BIGINT_MAX, description="계근기록 ID(정수)")]


def _require_aware_iso(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("ISO 8601 형식이 아닙니다") from None
    if parsed.tzinfo is None:
        raise ValueError("타임존 정보가 없습니다")
    return value


class _Response(BaseModel):
    # extra="ignore": 허용 목록에 없는 필드는 결과에서 빠진다.
    model_config = ConfigDict(extra="ignore", strict=True)


class WeighingRecord(_Response):
    """목록 항목. 중량 단위는 kg 정수, 시각은 API가 준 UTC(+00:00) 문자열 그대로다."""

    id: int
    ticket_no: str
    direction: Direction
    status: Status
    vehicle_no: str
    partner_name: str
    item_name: str
    operator_name: str
    gross_weight_kg: int | None
    tare_weight_kg: int | None
    net_weight_kg: int | None
    first_weighed_at: str | None
    created_at: str
    updated_at: str

    @field_validator("first_weighed_at", "created_at", "updated_at")
    @classmethod
    def _check_datetime(cls, value: str | None) -> str | None:
        return _require_aware_iso(value)


class WeighingRecordDetail(WeighingRecord):
    """단건 상세. 취소 관련 필드는 상세에만 있다."""

    cancelled_at: str | None
    cancelled_by: str | None
    cancel_reason: str | None

    @field_validator("cancelled_at")
    @classmethod
    def _check_cancelled_at(cls, value: str | None) -> str | None:
        return _require_aware_iso(value)


class WeighingRecordList(_Response):
    items: list[WeighingRecord]
    page: int
    page_size: int
    total: int
    total_pages: int
