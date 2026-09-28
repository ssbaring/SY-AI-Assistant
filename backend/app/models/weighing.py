from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.services.weighing_limits import MAX_WEIGHT_KG, MIN_WEIGHT_KG


class WeighingRecord(Base):
    """계근기록 현재값 1건 = 거래 1건.

    총중량(gross)과 공차(tare)를 한 행에 함께 둔다. 입고는 총중량이 먼저,
    출고는 공차가 먼저 들어올 수 있어 '1차/2차' 대신 역할 이름을 쓰고,
    어느 쪽이 먼저인지는 각자의 계근 시각으로 알 수 있다. 순서 자체는
    DB에서 강제하지 않는다(정책 합의 사항).
    """

    __tablename__ = "weighing_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # 계근번호는 DB 트리거가 자동 채번한다(YYYYMMDD-0001, 날짜별 초기화).
    # 애플리케이션이 값을 주지 않으면(NULL) 트리거가 채우므로 여기서는 그냥 서버 기본값 없이 둔다.
    ticket_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)

    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="IN_PROGRESS")

    # MVP: 거래처·품목은 마스터 테이블 없이 스냅샷 문자열로 저장한다.
    partner_name: Mapped[str] = mapped_column(String(100), nullable=False)
    item_name: Mapped[str] = mapped_column(String(100), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(50), nullable=False)

    # 원문(raw)은 입력값을 그대로 보존하고, 정규화(norm)는 검색·중복 검사에 쓴다.
    vehicle_no_raw: Mapped[str] = mapped_column(String(20), nullable=False)
    vehicle_no_norm: Mapped[str] = mapped_column(String(20), nullable=False)

    gross_weight_kg: Mapped[int | None] = mapped_column(Integer)
    gross_weighed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gross_source: Mapped[str | None] = mapped_column(String(10))
    gross_entered_by: Mapped[str | None] = mapped_column(String(50))
    gross_manual_reason: Mapped[str | None] = mapped_column(Text)

    tare_weight_kg: Mapped[int | None] = mapped_column(Integer)
    tare_weighed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tare_source: Mapped[str | None] = mapped_column(String(10))
    tare_entered_by: Mapped[str | None] = mapped_column(String(50))
    tare_manual_reason: Mapped[str | None] = mapped_column(Text)

    # 실중량은 DB가 계산한다(서비스 코드가 따로 계산해 저장하지 않는다).
    # 한쪽이 NULL이면 자동으로 NULL이 되어 '진행 중' 상태를 그대로 반영한다.
    net_weight_kg: Mapped[int | None] = mapped_column(
        Integer, Computed("gross_weight_kg - tare_weight_kg", persisted=True)
    )
    # 기간 조회 기준 시각: 두 계근 시각 중 빠른 쪽. LEAST()는 NULL을 무시하고
    # 값이 있는 쪽을 반환하므로 '진행 중' 상태에서도 값이 채워진다.
    first_weighed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        Computed("LEAST(gross_weighed_at, tare_weighed_at)", persisted=True),
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[str | None] = mapped_column(String(50))
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 트리거가 UPDATE 때마다 now()로 갱신한다(서비스 코드가 아직 없기 때문).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("direction IN ('INBOUND','OUTBOUND')", name="direction_allowed"),
        CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','CANCELED')", name="status_allowed"),
        CheckConstraint(
            f"gross_weight_kg IS NULL OR gross_weight_kg BETWEEN {MIN_WEIGHT_KG} AND {MAX_WEIGHT_KG}",
            name="gross_weight_range",
        ),
        CheckConstraint(
            f"tare_weight_kg IS NULL OR tare_weight_kg BETWEEN {MIN_WEIGHT_KG} AND {MAX_WEIGHT_KG}",
            name="tare_weight_range",
        ),
        CheckConstraint(
            "gross_weight_kg IS NULL OR tare_weight_kg IS NULL OR gross_weight_kg >= tare_weight_kg",
            name="gross_ge_tare",
        ),
        CheckConstraint(
            "gross_weight_kg IS NOT NULL OR tare_weight_kg IS NOT NULL",
            name="weight_present",
        ),
        CheckConstraint(
            "(gross_weight_kg IS NULL) = (gross_weighed_at IS NULL) "
            "AND (gross_weight_kg IS NULL) = (gross_source IS NULL)",
            name="gross_set_consistency",
        ),
        CheckConstraint(
            "(tare_weight_kg IS NULL) = (tare_weighed_at IS NULL) "
            "AND (tare_weight_kg IS NULL) = (tare_source IS NULL)",
            name="tare_set_consistency",
        ),
        CheckConstraint(
            "gross_source IS NULL OR gross_source IN ('SCALE','MANUAL','IMPORT')",
            name="gross_source_allowed",
        ),
        CheckConstraint(
            "tare_source IS NULL OR tare_source IN ('SCALE','MANUAL','IMPORT')",
            name="tare_source_allowed",
        ),
        CheckConstraint(
            "gross_source IS DISTINCT FROM 'MANUAL' OR "
            "(btrim(coalesce(gross_entered_by,'')) <> '' AND btrim(coalesce(gross_manual_reason,'')) <> '')",
            name="gross_manual_requires_reason",
        ),
        CheckConstraint(
            "tare_source IS DISTINCT FROM 'MANUAL' OR "
            "(btrim(coalesce(tare_entered_by,'')) <> '' AND btrim(coalesce(tare_manual_reason,'')) <> '')",
            name="tare_manual_requires_reason",
        ),
        CheckConstraint(
            "status <> 'COMPLETED' OR (gross_weight_kg IS NOT NULL AND tare_weight_kg IS NOT NULL)",
            name="completed_requires_both_weights",
        ),
        CheckConstraint(
            "status <> 'IN_PROGRESS' OR (gross_weight_kg IS NULL OR tare_weight_kg IS NULL)",
            name="in_progress_weight_incomplete",
        ),
        CheckConstraint(
            f"status <> 'COMPLETED' OR (gross_weight_kg - tare_weight_kg) >= {MIN_WEIGHT_KG}",
            name="completed_net_weight_min",
        ),
        CheckConstraint(
            "(status = 'CANCELED' AND cancelled_at IS NOT NULL "
            "AND btrim(coalesce(cancelled_by,'')) <> '' AND btrim(coalesce(cancel_reason,'')) <> '') "
            "OR (status <> 'CANCELED' AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancel_reason IS NULL)",
            name="cancel_fields_consistency",
        ),
        CheckConstraint(
            "btrim(ticket_no) <> '' AND btrim(partner_name) <> '' AND btrim(item_name) <> '' "
            "AND btrim(operator_name) <> '' AND btrim(vehicle_no_raw) <> '' AND btrim(vehicle_no_norm) <> ''",
            name="required_text_not_blank",
        ),
        CheckConstraint(
            "vehicle_no_norm !~ '[[:space:]-]'",
            name="vehicle_no_norm_format",
        ),
        Index("ix_weighing_records_first_weighed_at", "first_weighed_at"),
        Index("ix_weighing_records_partner_first", "partner_name", "first_weighed_at"),
        Index("ix_weighing_records_vehicle_first", "vehicle_no_norm", "first_weighed_at"),
        Index(
            "ix_weighing_records_open",
            "first_weighed_at",
            postgresql_where=text("status = 'IN_PROGRESS'"),
        ),
    )


class WeighingRecordHistory(Base):
    """추가 전용 변경 이력. UPDATE·DELETE는 트리거가 막는다.

    누가·왜 바꿨는지(작업자, 사유, 승인자)는 이번 단계에서 제외한 인증·승인
    서비스가 붙을 때 함께 추가한다. 지금은 DB 트리거가 자동으로 변경 전/후
    값만 남긴다.
    """

    __tablename__ = "weighing_record_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    weighing_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("weighing_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("event_type IN ('CREATED','UPDATED')", name="event_type_allowed"),
        Index("ix_weighing_record_history_record", "weighing_record_id", "occurred_at"),
    )
