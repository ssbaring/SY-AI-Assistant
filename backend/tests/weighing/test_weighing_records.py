"""weighing_records / weighing_record_history 스키마 자체를 검증한다.

이번 단계에는 서비스·API 계층이 없으므로, 실제로 이 테이블에 값을 넣을
주체(2단계 서비스)를 흉내 내어 원시 SQL로 직접 INSERT/UPDATE하면서 DB
제약과 트리거가 의도대로 동작하는지 확인한다.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from psycopg.errors import CheckViolation, RaiseException, UniqueViolation

from app.services.weighing_limits import MAX_WEIGHT_KG, MIN_WEIGHT_KG
from tests.weighing.conftest import connect

BASE_FIELDS = dict(
    direction="INBOUND",
    partner_name="TEST-partner",
    item_name="TEST-item",
    operator_name="TEST-operator",
    vehicle_no_raw="12GA 3456",
    vehicle_no_norm="12GA3456",
)


def insert_in_progress(conn, **overrides):
    """총중량만 있는 '진행 중' 건 하나를 등록하고 (id, ticket_no)를 반환한다."""

    fields = {**BASE_FIELDS, **overrides}
    fields.setdefault("gross_weight_kg", 15000)
    fields.setdefault("gross_source", "SCALE")
    columns = list(fields.keys()) + ["gross_weighed_at"]
    placeholders = [f"%({k})s" for k in fields] + ["now()"]
    sql = (
        f"INSERT INTO weighing_records ({', '.join(columns)}) "
        f"VALUES ({', '.join(placeholders)}) RETURNING id, ticket_no"
    )
    row = conn.execute(sql, fields).fetchone()
    return row[0], row[1]


def complete_with_tare(conn, record_id, tare_weight_kg):
    conn.execute(
        "UPDATE weighing_records SET tare_weight_kg=%s, tare_weighed_at=now(), "
        "tare_source='SCALE', status='COMPLETED' WHERE id=%s",
        (tare_weight_kg, record_id),
    )


def cancel(conn, record_id, cancelled_by="TEST-op", cancel_reason="TEST-reason"):
    conn.execute(
        "UPDATE weighing_records SET status='CANCELED', cancelled_at=now(), "
        "cancelled_by=%s, cancel_reason=%s WHERE id=%s",
        (cancelled_by, cancel_reason, record_id),
    )


# ---------------------------------------------------------------------------
# 3. 계근번호 자동 채번과 중복 방지
# ---------------------------------------------------------------------------


def test_ticket_no_is_auto_assigned_with_expected_format(raw_conn):
    _id, ticket_no = insert_in_progress(raw_conn)
    # YYYYMMDD-0001 형식만 확인한다. 자정 부근에는 서버 날짜(Asia/Seoul 기준)와
    # 테스트를 실행하는 로컬 시각의 날짜가 다를 수 있어 날짜값 자체는 비교하지 않는다.
    assert len(ticket_no) == 13
    assert ticket_no[8] == "-"
    assert ticket_no[:8].isdigit()
    assert ticket_no[9:].isdigit()


def test_ticket_no_sequence_resets_are_not_tested_across_days_but_increments_within_a_day(raw_conn):
    _, first = insert_in_progress(raw_conn, vehicle_no_norm="AA0001")
    _, second = insert_in_progress(raw_conn, vehicle_no_norm="AA0002")
    first_date, first_seq = first.split("-")
    second_date, second_seq = second.split("-")
    assert first_date == second_date
    assert int(second_seq) == int(first_seq) + 1


def test_duplicate_ticket_no_is_rejected(raw_conn):
    _, ticket_no = insert_in_progress(raw_conn)
    with pytest.raises(UniqueViolation):
        insert_in_progress(raw_conn, ticket_no=ticket_no, vehicle_no_norm="ZZ9999")


def test_canceled_ticket_no_cannot_be_reused(raw_conn):
    record_id, ticket_no = insert_in_progress(raw_conn)
    cancel(raw_conn, record_id)
    with pytest.raises(UniqueViolation):
        insert_in_progress(raw_conn, ticket_no=ticket_no, vehicle_no_norm="ZZ8888")


def test_ticket_numbering_is_safe_under_concurrent_inserts():
    """조회 후 +1이 아니라 DB 수준 원자적 증가인지 실제 동시 커넥션으로 확인한다."""

    from app.core.config import settings

    def _insert(i: int) -> str:
        with connect(settings.database_url) as conn:
            _, ticket_no = insert_in_progress(conn, vehicle_no_norm=f"CONC{i:03d}")
            return ticket_no

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_insert, range(16)))

    assert len(results) == len(set(results)), f"중복 계근번호 발생: {results}"


# ---------------------------------------------------------------------------
# 5. 중량 하한·상한
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("weight", [0, -1, MAX_WEIGHT_KG + 1])
def test_weight_outside_allowed_range_is_rejected(raw_conn, weight):
    with pytest.raises(CheckViolation):
        insert_in_progress(raw_conn, gross_weight_kg=weight)


@pytest.mark.parametrize("weight", [MIN_WEIGHT_KG, MAX_WEIGHT_KG])
def test_weight_at_boundary_is_accepted(raw_conn, weight):
    record_id, _ = insert_in_progress(raw_conn, gross_weight_kg=weight)
    assert record_id is not None


def test_gross_must_be_greater_or_equal_to_tare(raw_conn):
    record_id, _ = insert_in_progress(raw_conn, gross_weight_kg=5000)
    with pytest.raises(CheckViolation):
        complete_with_tare(raw_conn, record_id, tare_weight_kg=6000)


# ---------------------------------------------------------------------------
# 6. 완료 시 실중량 검증
# ---------------------------------------------------------------------------


def test_completed_requires_net_weight_at_least_minimum(raw_conn):
    record_id, _ = insert_in_progress(raw_conn, gross_weight_kg=5000)
    with pytest.raises(CheckViolation):
        # 총중량 == 공차 -> 실중량 0 -> COMPLETED에서는 거부되어야 한다.
        complete_with_tare(raw_conn, record_id, tare_weight_kg=5000)


def test_completed_with_valid_net_weight_succeeds(raw_conn):
    record_id, _ = insert_in_progress(raw_conn, gross_weight_kg=5000)
    complete_with_tare(raw_conn, record_id, tare_weight_kg=5000 - MIN_WEIGHT_KG)
    row = raw_conn.execute(
        "SELECT status, net_weight_kg FROM weighing_records WHERE id=%s", (record_id,)
    ).fetchone()
    assert row == ("COMPLETED", MIN_WEIGHT_KG)


def test_manual_source_requires_actor_and_reason(raw_conn):
    with pytest.raises(CheckViolation):
        insert_in_progress(
            raw_conn,
            gross_source="MANUAL",
            gross_entered_by=None,
            gross_manual_reason=None,
        )


# ---------------------------------------------------------------------------
# 7. 수정 이력 보존
# ---------------------------------------------------------------------------


def test_insert_and_update_are_logged_to_history(raw_conn):
    record_id, _ = insert_in_progress(raw_conn, gross_weight_kg=9000)
    complete_with_tare(raw_conn, record_id, tare_weight_kg=4000)

    rows = raw_conn.execute(
        "SELECT event_type, before_data, after_data FROM weighing_record_history "
        "WHERE weighing_record_id=%s ORDER BY id",
        (record_id,),
    ).fetchall()

    assert [r[0] for r in rows] == ["CREATED", "UPDATED"]
    created_event, updated_event = rows
    assert created_event[1] is None
    assert created_event[2]["gross_weight_kg"] == 9000
    assert updated_event[1]["status"] == "IN_PROGRESS"
    assert updated_event[2]["status"] == "COMPLETED"
    assert updated_event[2]["net_weight_kg"] == 5000


def test_no_op_update_does_not_create_history_row(raw_conn):
    """updated_at은 모든 UPDATE에서 바뀌지만, 업무 값이 그대로면 이력에는 남기지 않는다."""

    record_id, _ = insert_in_progress(raw_conn)
    before_count, before_updated_at = raw_conn.execute(
        "SELECT (SELECT count(*) FROM weighing_record_history WHERE weighing_record_id=%s), "
        "(SELECT updated_at FROM weighing_records WHERE id=%s)",
        (record_id, record_id),
    ).fetchone()

    # 같은 값으로 UPDATE (업무상 실제 변경 없음)
    raw_conn.execute(
        "UPDATE weighing_records SET partner_name = partner_name WHERE id=%s", (record_id,)
    )

    after_count, after_updated_at = raw_conn.execute(
        "SELECT (SELECT count(*) FROM weighing_record_history WHERE weighing_record_id=%s), "
        "(SELECT updated_at FROM weighing_records WHERE id=%s)",
        (record_id, record_id),
    ).fetchone()
    assert after_count == before_count
    assert after_updated_at > before_updated_at


# ---------------------------------------------------------------------------
# 8. 물리적 삭제 방지
# ---------------------------------------------------------------------------


def test_weighing_record_cannot_be_deleted(raw_conn):
    record_id, _ = insert_in_progress(raw_conn)
    with pytest.raises(RaiseException):
        raw_conn.execute("DELETE FROM weighing_records WHERE id=%s", (record_id,))


def test_history_row_cannot_be_deleted(raw_conn):
    record_id, _ = insert_in_progress(raw_conn)
    history_id = raw_conn.execute(
        "SELECT id FROM weighing_record_history WHERE weighing_record_id=%s", (record_id,)
    ).fetchone()[0]
    with pytest.raises(RaiseException):
        raw_conn.execute("DELETE FROM weighing_record_history WHERE id=%s", (history_id,))


def test_history_row_cannot_be_updated(raw_conn):
    record_id, _ = insert_in_progress(raw_conn)
    history_id = raw_conn.execute(
        "SELECT id FROM weighing_record_history WHERE weighing_record_id=%s", (record_id,)
    ).fetchone()[0]
    with pytest.raises(RaiseException):
        raw_conn.execute(
            "UPDATE weighing_record_history SET event_type='CREATED' WHERE id=%s", (history_id,)
        )


def test_history_survives_when_referenced_record_delete_is_attempted(raw_conn):
    """FK가 RESTRICT이므로, 트리거가 없더라도 이력이 있는 기록은 삭제될 수 없다."""

    record_id, _ = insert_in_progress(raw_conn)
    with pytest.raises(RaiseException):
        raw_conn.execute("DELETE FROM weighing_records WHERE id=%s", (record_id,))
    remaining = raw_conn.execute(
        "SELECT count(*) FROM weighing_record_history WHERE weighing_record_id=%s", (record_id,)
    ).fetchone()[0]
    assert remaining >= 1


# ---------------------------------------------------------------------------
# 취소 기록 불변성: CANCELED로의 최초 전환은 허용하되, 이후 UPDATE는 모두 차단한다.
# ---------------------------------------------------------------------------


def test_normal_record_can_transition_to_canceled(raw_conn):
    record_id, _ = insert_in_progress(raw_conn)
    cancel(raw_conn, record_id)
    status = raw_conn.execute(
        "SELECT status FROM weighing_records WHERE id=%s", (record_id,)
    ).fetchone()[0]
    assert status == "CANCELED"


def test_weight_cannot_change_after_canceled(raw_conn):
    record_id, _ = insert_in_progress(raw_conn, gross_weight_kg=5000)
    cancel(raw_conn, record_id)
    with pytest.raises(RaiseException):
        raw_conn.execute(
            "UPDATE weighing_records SET gross_weight_kg=6000 WHERE id=%s", (record_id,)
        )


@pytest.mark.parametrize(
    "column,new_value",
    [
        ("vehicle_no_raw", "99ZZ 9999"),
        ("vehicle_no_norm", "99ZZ9999"),
        ("partner_name", "TEST-changed-partner"),
        ("item_name", "TEST-changed-item"),
    ],
)
def test_business_fields_cannot_change_after_canceled(raw_conn, column, new_value):
    record_id, _ = insert_in_progress(raw_conn)
    cancel(raw_conn, record_id)
    with pytest.raises(RaiseException):
        raw_conn.execute(
            f"UPDATE weighing_records SET {column}=%s WHERE id=%s", (new_value, record_id)
        )


def test_cancel_transition_itself_is_still_logged_to_history(raw_conn):
    record_id, _ = insert_in_progress(raw_conn)
    cancel(raw_conn, record_id, cancelled_by="TEST-canceler", cancel_reason="TEST-why")

    rows = raw_conn.execute(
        "SELECT event_type, after_data FROM weighing_record_history "
        "WHERE weighing_record_id=%s ORDER BY id",
        (record_id,),
    ).fetchall()

    assert [r[0] for r in rows] == ["CREATED", "UPDATED"]
    after = rows[-1][1]
    assert after["status"] == "CANCELED"
    assert after["cancelled_by"] == "TEST-canceler"
    assert after["cancel_reason"] == "TEST-why"


def test_further_update_after_canceled_does_not_add_history(raw_conn):
    record_id, _ = insert_in_progress(raw_conn)
    cancel(raw_conn, record_id)
    before = raw_conn.execute(
        "SELECT count(*) FROM weighing_record_history WHERE weighing_record_id=%s", (record_id,)
    ).fetchone()[0]

    with pytest.raises(RaiseException):
        raw_conn.execute(
            "UPDATE weighing_records SET operator_name='TEST-someone-else' WHERE id=%s",
            (record_id,),
        )

    after = raw_conn.execute(
        "SELECT count(*) FROM weighing_record_history WHERE weighing_record_id=%s", (record_id,)
    ).fetchone()[0]
    assert after == before
