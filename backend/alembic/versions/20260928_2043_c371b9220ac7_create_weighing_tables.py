"""create weighing tables

Revision ID: c371b9220ac7
Revises:
Create Date: 2026-09-28 20:43:58.698570

시각 정책: 모든 timestamp 컬럼은 timestamptz로 UTC를 저장한다. 계근번호의
YYYYMMDD 부분만 예외로 Asia/Seoul 달력 날짜를 기준으로 계산한다(공장이
한국에 있고, 계근번호는 "그날 업무"를 가리키는 사람이 읽는 식별자이기
때문이다). 저장 자체는 여전히 UTC이며, 날짜 계산에만 시간대 변환이
들어간다. 자세한 내용은 assign_weighing_ticket_no() 함수 참고.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# 마이그레이션은 애플리케이션 코드(app.services.weighing_limits)를 import하지
# 않는다. 마이그레이션은 그 시점의 스키마를 영구히 기록하는 문서이므로, 나중에
# 애플리케이션 상수가 바뀌어도 이 리비전이 재현하는 결과는 항상 같아야 한다.
# 아래 두 값은 이 리비전을 작성한 시점의 값을 그대로 고정한 것이다. 실제 검증
# 로직(향후 서비스 계층의 입력 검증)은 app.services.weighing_limits를 계속
# 사용하며, tests/weighing/test_migrations.py가 두 값이 아직 일치하는지
# 확인한다 — 값이 달라지면 새 마이그레이션으로 반영해야 한다.
MIN_WEIGHT_KG = 1
MAX_WEIGHT_KG = 200000

# revision identifiers, used by Alembic.
revision: str = "c371b9220ac7"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 날짜별 계근번호 채번용 카운터. 테이블 하나로 (날짜, 다음 번호)를 관리하고,
    #    실제 증가는 아래 next_ticket_no() 함수의 UPSERT가 원자적으로 처리한다.
    op.create_table(
        "weighing_ticket_sequences",
        sa.Column("ticket_date", sa.Date(), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("ticket_date", name="pk_weighing_ticket_sequences"),
    )

    # 2. 계근기록 현재값 테이블 (거래 1건 = 1행).
    op.create_table(
        "weighing_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("ticket_no", sa.String(length=20), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'IN_PROGRESS'"),
        ),
        sa.Column("partner_name", sa.String(length=100), nullable=False),
        sa.Column("item_name", sa.String(length=100), nullable=False),
        sa.Column("operator_name", sa.String(length=50), nullable=False),
        sa.Column("vehicle_no_raw", sa.String(length=20), nullable=False),
        sa.Column("vehicle_no_norm", sa.String(length=20), nullable=False),
        sa.Column("gross_weight_kg", sa.Integer(), nullable=True),
        sa.Column("gross_weighed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gross_source", sa.String(length=10), nullable=True),
        sa.Column("gross_entered_by", sa.String(length=50), nullable=True),
        sa.Column("gross_manual_reason", sa.Text(), nullable=True),
        sa.Column("tare_weight_kg", sa.Integer(), nullable=True),
        sa.Column("tare_weighed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tare_source", sa.String(length=10), nullable=True),
        sa.Column("tare_entered_by", sa.String(length=50), nullable=True),
        sa.Column("tare_manual_reason", sa.Text(), nullable=True),
        sa.Column(
            "net_weight_kg",
            sa.Integer(),
            sa.Computed("gross_weight_kg - tare_weight_kg", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "first_weighed_at",
            sa.DateTime(timezone=True),
            sa.Computed("LEAST(gross_weighed_at, tare_weighed_at)", persisted=True),
            nullable=True,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.String(length=50), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_weighing_records"),
        sa.UniqueConstraint("ticket_no", name="uq_weighing_records_ticket_no"),
        sa.CheckConstraint(
            "direction IN ('INBOUND','OUTBOUND')",
            name="direction_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS','COMPLETED','CANCELED')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            f"gross_weight_kg IS NULL OR gross_weight_kg BETWEEN {MIN_WEIGHT_KG} AND {MAX_WEIGHT_KG}",
            name="gross_weight_range",
        ),
        sa.CheckConstraint(
            f"tare_weight_kg IS NULL OR tare_weight_kg BETWEEN {MIN_WEIGHT_KG} AND {MAX_WEIGHT_KG}",
            name="tare_weight_range",
        ),
        sa.CheckConstraint(
            "gross_weight_kg IS NULL OR tare_weight_kg IS NULL OR gross_weight_kg >= tare_weight_kg",
            name="gross_ge_tare",
        ),
        sa.CheckConstraint(
            "gross_weight_kg IS NOT NULL OR tare_weight_kg IS NOT NULL",
            name="weight_present",
        ),
        sa.CheckConstraint(
            "(gross_weight_kg IS NULL) = (gross_weighed_at IS NULL) "
            "AND (gross_weight_kg IS NULL) = (gross_source IS NULL)",
            name="gross_set_consistency",
        ),
        sa.CheckConstraint(
            "(tare_weight_kg IS NULL) = (tare_weighed_at IS NULL) "
            "AND (tare_weight_kg IS NULL) = (tare_source IS NULL)",
            name="tare_set_consistency",
        ),
        sa.CheckConstraint(
            "gross_source IS NULL OR gross_source IN ('SCALE','MANUAL','IMPORT')",
            name="gross_source_allowed",
        ),
        sa.CheckConstraint(
            "tare_source IS NULL OR tare_source IN ('SCALE','MANUAL','IMPORT')",
            name="tare_source_allowed",
        ),
        sa.CheckConstraint(
            "gross_source IS DISTINCT FROM 'MANUAL' OR "
            "(btrim(coalesce(gross_entered_by,'')) <> '' AND btrim(coalesce(gross_manual_reason,'')) <> '')",
            name="gross_manual_requires_reason",
        ),
        sa.CheckConstraint(
            "tare_source IS DISTINCT FROM 'MANUAL' OR "
            "(btrim(coalesce(tare_entered_by,'')) <> '' AND btrim(coalesce(tare_manual_reason,'')) <> '')",
            name="tare_manual_requires_reason",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETED' OR (gross_weight_kg IS NOT NULL AND tare_weight_kg IS NOT NULL)",
            name="completed_requires_both_weights",
        ),
        sa.CheckConstraint(
            "status <> 'IN_PROGRESS' OR (gross_weight_kg IS NULL OR tare_weight_kg IS NULL)",
            name="in_progress_weight_incomplete",
        ),
        sa.CheckConstraint(
            f"status <> 'COMPLETED' OR (gross_weight_kg - tare_weight_kg) >= {MIN_WEIGHT_KG}",
            name="completed_net_weight_min",
        ),
        sa.CheckConstraint(
            "(status = 'CANCELED' AND cancelled_at IS NOT NULL "
            "AND btrim(coalesce(cancelled_by,'')) <> '' AND btrim(coalesce(cancel_reason,'')) <> '') "
            "OR (status <> 'CANCELED' AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancel_reason IS NULL)",
            name="cancel_fields_consistency",
        ),
        sa.CheckConstraint(
            "btrim(ticket_no) <> '' AND btrim(partner_name) <> '' AND btrim(item_name) <> '' "
            "AND btrim(operator_name) <> '' AND btrim(vehicle_no_raw) <> '' AND btrim(vehicle_no_norm) <> ''",
            name="required_text_not_blank",
        ),
        sa.CheckConstraint(
            "vehicle_no_norm !~ '[[:space:]-]'",
            name="vehicle_no_norm_format",
        ),
    )
    op.create_index(
        "ix_weighing_records_first_weighed_at", "weighing_records", ["first_weighed_at"]
    )
    op.create_index(
        "ix_weighing_records_partner_first",
        "weighing_records",
        ["partner_name", "first_weighed_at"],
    )
    op.create_index(
        "ix_weighing_records_vehicle_first",
        "weighing_records",
        ["vehicle_no_norm", "first_weighed_at"],
    )
    op.create_index(
        "ix_weighing_records_open",
        "weighing_records",
        ["first_weighed_at"],
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )

    # 3. 추가 전용 변경 이력 테이블. 작업자·사유·승인자는 인증 단계에서 함께 추가한다.
    op.create_table(
        "weighing_record_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("weighing_record_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("before_data", JSONB(), nullable=True),
        sa.Column("after_data", JSONB(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_weighing_record_history"),
        sa.ForeignKeyConstraint(
            ["weighing_record_id"],
            ["weighing_records.id"],
            name="fk_weighing_record_history_weighing_record_id_weighing_records",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "event_type IN ('CREATED','UPDATED')",
            name="event_type_allowed",
        ),
    )
    op.create_index(
        "ix_weighing_record_history_record",
        "weighing_record_history",
        ["weighing_record_id", "occurred_at"],
    )

    # 4. 함수: 계근번호 채번, updated_at 갱신, 변경 이력 자동 기록, 삭제·수정 금지.
    op.execute(
        """
        CREATE FUNCTION next_ticket_no(p_date date) RETURNS text AS $$
        DECLARE
          v_seq integer;
        BEGIN
          -- ON CONFLICT DO UPDATE는 같은 ticket_date 행에 대해 자동으로 직렬화되므로,
          -- 동시에 여러 요청이 들어와도 "조회 후 +1" 방식과 달리 값을 잃어버리지 않는다.
          INSERT INTO weighing_ticket_sequences (ticket_date, last_seq)
          VALUES (p_date, 1)
          ON CONFLICT (ticket_date) DO UPDATE
            SET last_seq = weighing_ticket_sequences.last_seq + 1
          RETURNING last_seq INTO v_seq;
          RETURN to_char(p_date, 'YYYYMMDD') || '-' || lpad(v_seq::text, 4, '0');
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        -- 정책: DB에 저장되는 시각(gross_weighed_at 등)은 전부 UTC(timestamptz)다.
        -- 계근번호의 날짜 부분만 예외로 Asia/Seoul 달력 날짜를 쓴다. "now() AT TIME
        -- ZONE 'Asia/Seoul'"은 UTC 타임스탬프를 서울 시각 기준 값으로 변환한 뒤
        -- 날짜만 취하는 것이며, 실제 저장값의 시간대를 바꾸는 것이 아니다.
        CREATE FUNCTION assign_weighing_ticket_no() RETURNS trigger AS $$
        BEGIN
          IF NEW.ticket_no IS NULL THEN
            NEW.ticket_no := next_ticket_no((now() AT TIME ZONE 'Asia/Seoul')::date);
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE FUNCTION set_weighing_record_updated_at() RETURNS trigger AS $$
        BEGIN
          NEW.updated_at := now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE FUNCTION log_weighing_record_history() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            INSERT INTO weighing_record_history (weighing_record_id, event_type, before_data, after_data)
            VALUES (NEW.id, 'CREATED', NULL, to_jsonb(NEW));
          ELSIF TG_OP = 'UPDATE' THEN
            -- updated_at은 매 UPDATE마다 트리거가 자동으로 바꾸므로 비교에서 제외한다.
            -- 그렇지 않으면 값이 실제로 바뀌지 않은 UPDATE도 이력에 쌓여 노이즈가 된다.
            IF (to_jsonb(OLD) - 'updated_at') IS DISTINCT FROM (to_jsonb(NEW) - 'updated_at') THEN
              INSERT INTO weighing_record_history (weighing_record_id, event_type, before_data, after_data)
              VALUES (NEW.id, 'UPDATED', to_jsonb(OLD), to_jsonb(NEW));
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE FUNCTION forbid_row_delete() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'delete is not allowed on %; cancel the record instead of deleting it', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE FUNCTION forbid_row_update() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only and cannot be modified', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        -- 이미 CANCELED인 행은 감사·증빙 보존을 위해 더 이상 바뀌지 않아야 한다.
        -- OLD.status만 보고 판단하므로, 정상 기록을 CANCELED로 "전환"하는 최초
        -- UPDATE는 막지 않는다(그때는 OLD.status가 아직 CANCELED가 아니다).
        -- 막힌 UPDATE는 트랜잭션 전체가 취소되어 이력에도 남지 않는다(원래 실행되지
        -- 않은 것과 같다).
        CREATE FUNCTION forbid_update_when_canceled() RETURNS trigger AS $$
        BEGIN
          IF OLD.status = 'CANCELED' THEN
            RAISE EXCEPTION 'weighing_records.id=%: canceled records cannot be modified', OLD.id;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # 5. 트리거 부착.
    op.execute(
        """
        CREATE TRIGGER trg_weighing_records_assign_ticket_no
        BEFORE INSERT ON weighing_records
        FOR EACH ROW EXECUTE FUNCTION assign_weighing_ticket_no();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_weighing_records_set_updated_at
        BEFORE UPDATE ON weighing_records
        FOR EACH ROW EXECUTE FUNCTION set_weighing_record_updated_at();
        """
    )
    op.execute(
        """
        -- 트리거 이름은 알파벳 순으로 실행되므로("immutable" < "set_updated_at"),
        -- 이 트리거가 set_updated_at보다 먼저 실행되어 취소된 행이면 다른 처리 전에
        -- 바로 예외를 낸다.
        CREATE TRIGGER trg_weighing_records_immutable_when_canceled
        BEFORE UPDATE ON weighing_records
        FOR EACH ROW EXECUTE FUNCTION forbid_update_when_canceled();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_weighing_records_no_delete
        BEFORE DELETE ON weighing_records
        FOR EACH ROW EXECUTE FUNCTION forbid_row_delete();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_weighing_records_history
        AFTER INSERT OR UPDATE ON weighing_records
        FOR EACH ROW EXECUTE FUNCTION log_weighing_record_history();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_weighing_record_history_no_delete
        BEFORE DELETE ON weighing_record_history
        FOR EACH ROW EXECUTE FUNCTION forbid_row_delete();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_weighing_record_history_no_update
        BEFORE UPDATE ON weighing_record_history
        FOR EACH ROW EXECUTE FUNCTION forbid_row_update();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_weighing_record_history_no_update ON weighing_record_history"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_weighing_record_history_no_delete ON weighing_record_history"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_weighing_records_history ON weighing_records")
    op.execute("DROP TRIGGER IF EXISTS trg_weighing_records_no_delete ON weighing_records")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_weighing_records_immutable_when_canceled ON weighing_records"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_weighing_records_set_updated_at ON weighing_records"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_weighing_records_assign_ticket_no ON weighing_records"
    )

    op.execute("DROP FUNCTION IF EXISTS forbid_update_when_canceled()")
    op.execute("DROP FUNCTION IF EXISTS forbid_row_update()")
    op.execute("DROP FUNCTION IF EXISTS forbid_row_delete()")
    op.execute("DROP FUNCTION IF EXISTS log_weighing_record_history()")
    op.execute("DROP FUNCTION IF EXISTS set_weighing_record_updated_at()")
    op.execute("DROP FUNCTION IF EXISTS assign_weighing_ticket_no()")
    op.execute("DROP FUNCTION IF EXISTS next_ticket_no(date)")

    op.drop_table("weighing_record_history")
    op.drop_table("weighing_records")
    op.drop_table("weighing_ticket_sequences")
