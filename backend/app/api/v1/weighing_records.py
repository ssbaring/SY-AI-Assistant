"""GET /api/v1/weighing-records — 계근기록 조회 전용 API.

등록·수정·취소는 다루지 않는다(2단계 범위 밖).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.weighing_record import (
    WeighingRecordDetail,
    WeighingRecordListParams,
    WeighingRecordListResponse,
)
from app.services import weighing_records_query as query

router = APIRouter(prefix="/weighing-records", tags=["weighing-records"])

_NOT_FOUND_BY_ID = "계근기록을 찾을 수 없습니다"
_NOT_FOUND_BY_TICKET = "계근번호에 해당하는 기록을 찾을 수 없습니다"


@router.get("", response_model=WeighingRecordListResponse)
def list_weighing_records(
    params: Annotated[WeighingRecordListParams, Query()],
    db: Session = Depends(get_db),
) -> WeighingRecordListResponse:
    """계근기록 목록을 조회한다.

    - 날짜 범위: `started_from <= first_weighed_at < started_to`
      (`started_from` 포함, `started_to` 미포함).
    - `started_from`/`started_to`는 타임존 정보가 있는 값만 허용한다
      (예: `...Z`, `+09:00`). 타임존이 없으면 422.
    - `CANCELED` 상태도 기본 목록에 포함되며 `status` 필터로 구분할 수 있다.
    - 취소 관련 필드(`cancelled_at`/`cancelled_by`/`cancel_reason`)는
      목록에 포함되지 않는다(상세 조회 전용).
    """

    return query.list_weighing_records(db, params)


# 정적 경로(/ticket/{ticket_no})를 동적 ID 경로(/{record_id})보다 먼저 등록한다.
@router.get("/ticket/{ticket_no}", response_model=WeighingRecordDetail)
def get_weighing_record_by_ticket(ticket_no: str, db: Session = Depends(get_db)) -> WeighingRecordDetail:
    record = query.get_weighing_record_by_ticket_no(db, ticket_no)
    if record is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_BY_TICKET)
    return record


@router.get("/{record_id}", response_model=WeighingRecordDetail)
def get_weighing_record(record_id: int, db: Session = Depends(get_db)) -> WeighingRecordDetail:
    record = query.get_weighing_record_by_id(db, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_BY_ID)
    return record
