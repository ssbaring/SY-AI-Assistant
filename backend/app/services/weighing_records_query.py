"""계근기록 조회 전용 쿼리.

이 모듈은 조회만 한다(등록·수정·취소는 다루지 않는다). SQLAlchemy Core
select()를 바인드 파라미터로만 조립하고, 정렬 필드는 화이트리스트
딕셔너리를 거치므로 사용자 입력 문자열이 SQL로 직접 들어가는 경로가 없다.
"""

import math

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.weighing import WeighingRecord
from app.schemas.weighing_record import (
    WeighingRecordDetail,
    WeighingRecordListParams,
    WeighingRecordListResponse,
    WeighingRecordOut,
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_filters(stmt: Select, params: WeighingRecordListParams) -> Select:
    if params.direction is not None:
        stmt = stmt.where(WeighingRecord.direction == params.direction)
    if params.status is not None:
        stmt = stmt.where(WeighingRecord.status == params.status)
    if params.vehicle_number is not None:
        # vehicle_number는 스키마 단계에서 이미 정규화되어 있다(공백·하이픈 제거, 대문자).
        stmt = stmt.where(WeighingRecord.vehicle_no_norm == params.vehicle_number)
    if params.partner_name is not None:
        stmt = stmt.where(
            WeighingRecord.partner_name.ilike(f"%{_escape_like(params.partner_name)}%", escape="\\")
        )
    if params.material_name is not None:
        stmt = stmt.where(
            WeighingRecord.item_name.ilike(f"%{_escape_like(params.material_name)}%", escape="\\")
        )
    if params.ticket_no is not None:
        stmt = stmt.where(WeighingRecord.ticket_no == params.ticket_no)
    if params.started_from is not None:
        stmt = stmt.where(WeighingRecord.first_weighed_at >= params.started_from)
    if params.started_to is not None:
        stmt = stmt.where(WeighingRecord.first_weighed_at < params.started_to)
    return stmt


def _apply_sort(stmt: Select, sort_dir: str) -> Select:
    # sort_dir은 Pydantic Literal["asc","desc"]로 이미 제한되어 있다. 그래도
    # 문자열을 SQL에 꽂지 않고 분기로만 처리한다(화이트리스트 방식).
    if sort_dir == "asc":
        return stmt.order_by(WeighingRecord.first_weighed_at.asc(), WeighingRecord.id.asc())
    return stmt.order_by(WeighingRecord.first_weighed_at.desc(), WeighingRecord.id.desc())


def _common_fields(record: WeighingRecord) -> dict:
    return dict(
        id=record.id,
        ticket_no=record.ticket_no,
        direction=record.direction,
        status=record.status,
        vehicle_no=record.vehicle_no_raw,
        partner_name=record.partner_name,
        item_name=record.item_name,
        operator_name=record.operator_name,
        gross_weight_kg=record.gross_weight_kg,
        tare_weight_kg=record.tare_weight_kg,
        net_weight_kg=record.net_weight_kg,
        first_weighed_at=record.first_weighed_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_out(record: WeighingRecord) -> WeighingRecordOut:
    return WeighingRecordOut(**_common_fields(record))


def _to_detail(record: WeighingRecord) -> WeighingRecordDetail:
    return WeighingRecordDetail(
        **_common_fields(record),
        cancelled_at=record.cancelled_at,
        cancelled_by=record.cancelled_by,
        cancel_reason=record.cancel_reason,
    )


def list_weighing_records(
    db: Session, params: WeighingRecordListParams
) -> WeighingRecordListResponse:
    base_stmt = _apply_filters(select(WeighingRecord), params)

    total = db.execute(select(func.count()).select_from(base_stmt.subquery())).scalar_one()

    data_stmt = _apply_sort(base_stmt, params.sort_dir)
    data_stmt = data_stmt.limit(params.page_size).offset((params.page - 1) * params.page_size)
    records = db.execute(data_stmt).scalars().all()

    total_pages = math.ceil(total / params.page_size) if total > 0 else 0

    return WeighingRecordListResponse(
        items=[_to_out(record) for record in records],
        page=params.page,
        page_size=params.page_size,
        total=total,
        total_pages=total_pages,
    )


def get_weighing_record_by_id(db: Session, record_id: int) -> WeighingRecordDetail | None:
    record = db.get(WeighingRecord, record_id)
    return _to_detail(record) if record is not None else None


def get_weighing_record_by_ticket_no(db: Session, ticket_no: str) -> WeighingRecordDetail | None:
    record = db.execute(
        select(WeighingRecord).where(WeighingRecord.ticket_no == ticket_no)
    ).scalar_one_or_none()
    return _to_detail(record) if record is not None else None
