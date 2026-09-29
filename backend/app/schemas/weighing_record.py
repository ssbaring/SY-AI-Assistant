"""계근기록 조회 API의 요청·응답 스키마.

ORM 모델을 그대로 직렬화하지 않고, 이 스키마들을 통해서만 응답을 만든다
(app/services/weighing_records_query.py 참고).
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from app.services.vehicle_no import normalize_vehicle_no

Direction = Literal["INBOUND", "OUTBOUND"]
Status = Literal["IN_PROGRESS", "COMPLETED", "CANCELED"]
SortDir = Literal["asc", "desc"]


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc)


class WeighingRecordOut(BaseModel):
    """목록 응답 항목. 취소 관련 필드는 포함하지 않는다(상세 응답 전용)."""

    id: int
    ticket_no: str
    direction: Direction
    status: Status
    vehicle_no: str
    partner_name: str
    item_name: str
    operator_name: str
    # 진행 중(IN_PROGRESS) 상태에서는 총중량·공차·실중량·첫 계근 시각이
    # 아직 없을 수 있다(DB CHECK 제약과 GENERATED 컬럼이 그렇게 되어 있다).
    gross_weight_kg: int | None
    tare_weight_kg: int | None
    net_weight_kg: int | None
    first_weighed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("first_weighed_at", "created_at", "updated_at", when_used="json")
    def _serialize_datetime(self, value: datetime | None) -> str | None:
        converted = _to_utc(value)
        return converted.isoformat() if converted is not None else None


class WeighingRecordDetail(WeighingRecordOut):
    """단건(ID/계근번호) 상세 응답. 취소 관련 필드를 추가로 포함한다."""

    cancelled_at: datetime | None
    cancelled_by: str | None
    cancel_reason: str | None

    @field_serializer("cancelled_at", when_used="json")
    def _serialize_cancelled_at(self, value: datetime | None) -> str | None:
        converted = _to_utc(value)
        return converted.isoformat() if converted is not None else None


class WeighingRecordListResponse(BaseModel):
    items: list[WeighingRecordOut]
    page: int
    page_size: int
    total: int
    total_pages: int


def _reject_blank(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("빈 문자열은 필터 값으로 사용할 수 없습니다")
    return value


class WeighingRecordListParams(BaseModel):
    direction: Direction | None = None
    status: Status | None = None
    vehicle_number: str | None = None
    partner_name: str | None = None
    material_name: str | None = None
    ticket_no: str | None = None
    started_from: datetime | None = None
    started_to: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_dir: SortDir = "desc"

    @field_validator("partner_name", "material_name", "ticket_no", mode="after")
    @classmethod
    def _validate_blank_text(cls, value: str | None) -> str | None:
        return _reject_blank(value)

    @field_validator("vehicle_number", mode="after")
    @classmethod
    def _validate_vehicle_number(cls, value: str | None) -> str | None:
        value = _reject_blank(value)
        if value is None:
            return None
        normalized = normalize_vehicle_no(value)
        if not normalized:
            raise ValueError("차량번호를 정규화한 결과가 비어 있습니다")
        return normalized

    @field_validator("started_from", "started_to", mode="after")
    @classmethod
    def _require_timezone_and_convert_to_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "타임존 정보가 없는 날짜는 허용하지 않습니다 (예: ...Z 또는 +09:00 형식 사용)"
            )
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_date_range(self) -> "WeighingRecordListParams":
        if (
            self.started_from is not None
            and self.started_to is not None
            and self.started_from >= self.started_to
        ):
            raise ValueError(
                "started_from은 started_to보다 빨라야 합니다 (같은 시각도 허용하지 않습니다)"
            )
        return self
