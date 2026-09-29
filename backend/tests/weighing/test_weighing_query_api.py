"""GET /api/v1/weighing-records 조회 API 테스트.

데이터는 기존 test_weighing_records.py의 원시 SQL 헬퍼로 만들고, 정리는
tests/weighing/conftest.py의 clean_weighing_tables(autouse)가 매 테스트
뒤 TRUNCATE로 처리한다 — 기존 방식을 그대로 재사용한다(새 격리 방식을
만들지 않음).
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.weighing.test_weighing_records import cancel, complete_with_tare, insert_in_progress

BASE_URL = "/api/v1/weighing-records"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _set_gross_weighed_at(raw_conn, record_id: int, when: datetime) -> None:
    raw_conn.execute(
        "UPDATE weighing_records SET gross_weighed_at=%s WHERE id=%s", (when, record_id)
    )


# ---------------------------------------------------------------------------
# 목록: 빈 목록 / 페이지네이션
# ---------------------------------------------------------------------------


def test_list_empty(api_client):
    resp = api_client.get(BASE_URL)
    assert resp.status_code == 200
    assert resp.json() == {
        "items": [],
        "page": 1,
        "page_size": 20,
        "total": 0,
        "total_pages": 0,
    }


def test_list_default_pagination(raw_conn, api_client):
    for i in range(3):
        insert_in_progress(raw_conn, vehicle_no_norm=f"PAGE{i:03d}")

    resp = api_client.get(BASE_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total_pages"] == 1
    assert len(body["items"]) == 3


def test_list_page_size_boundaries(api_client):
    assert api_client.get(BASE_URL, params={"page_size": 100}).status_code == 200
    assert api_client.get(BASE_URL, params={"page_size": 101}).status_code == 422
    assert api_client.get(BASE_URL, params={"page": 0}).status_code == 422
    assert api_client.get(BASE_URL, params={"page_size": 0}).status_code == 422


def test_list_pagination_second_page(raw_conn, api_client):
    ids = []
    for i in range(5):
        record_id, _ = insert_in_progress(raw_conn, vehicle_no_norm=f"PG2{i:03d}")
        ids.append(record_id)

    resp = api_client.get(BASE_URL, params={"page": 2, "page_size": 2})
    body = resp.json()
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert body["total"] == 5
    assert body["total_pages"] == 3
    assert len(body["items"]) == 2


# ---------------------------------------------------------------------------
# 필터 단독
# ---------------------------------------------------------------------------


def test_filter_by_direction(raw_conn, api_client):
    insert_in_progress(raw_conn, direction="INBOUND", vehicle_no_norm="DIR0001")
    insert_in_progress(raw_conn, direction="OUTBOUND", vehicle_no_norm="DIR0002")

    resp = api_client.get(BASE_URL, params={"direction": "OUTBOUND"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["direction"] == "OUTBOUND"


def test_filter_by_status(raw_conn, api_client):
    insert_in_progress(raw_conn, vehicle_no_norm="STA0001")
    id2, _ = insert_in_progress(raw_conn, gross_weight_kg=5000, vehicle_no_norm="STA0002")
    complete_with_tare(raw_conn, id2, tare_weight_kg=1000)

    resp = api_client.get(BASE_URL, params={"status": "COMPLETED"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "COMPLETED"


def test_filter_by_vehicle_number_ignores_spaces_and_hyphens(raw_conn, api_client):
    insert_in_progress(raw_conn, vehicle_no_raw="12가-3456", vehicle_no_norm="12가3456")

    resp = api_client.get(BASE_URL, params={"vehicle_number": "12 가 3456"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["vehicle_no"] == "12가-3456"


def test_filter_vehicle_number_blank_is_422(api_client):
    assert api_client.get(BASE_URL, params={"vehicle_number": "   "}).status_code == 422


def test_filter_vehicle_number_normalizes_to_empty_is_422(api_client):
    assert api_client.get(BASE_URL, params={"vehicle_number": " - - "}).status_code == 422


def test_filter_partner_name_partial_case_insensitive(raw_conn, api_client):
    insert_in_progress(raw_conn, partner_name="Samsung Trading", vehicle_no_norm="PART0001")

    resp = api_client.get(BASE_URL, params={"partner_name": "samsung"})
    assert resp.json()["total"] == 1


def test_filter_material_name_partial_case_insensitive(raw_conn, api_client):
    insert_in_progress(raw_conn, item_name="Scrap Iron", vehicle_no_norm="MAT0001")

    resp = api_client.get(BASE_URL, params={"material_name": "IRON"})
    assert resp.json()["total"] == 1


def test_filter_ticket_no_exact_match(raw_conn, api_client):
    _id, ticket_no = insert_in_progress(raw_conn, vehicle_no_norm="TKT0001")
    insert_in_progress(raw_conn, vehicle_no_norm="TKT0002")

    resp = api_client.get(BASE_URL, params={"ticket_no": ticket_no})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["ticket_no"] == ticket_no


@pytest.mark.parametrize("field", ["partner_name", "material_name", "ticket_no"])
def test_string_filter_blank_is_422(api_client, field):
    assert api_client.get(BASE_URL, params={field: "   "}).status_code == 422


# ---------------------------------------------------------------------------
# 필터 조합
# ---------------------------------------------------------------------------


def test_combined_filters(raw_conn, api_client):
    insert_in_progress(
        raw_conn, direction="INBOUND", partner_name="Combo Partner", vehicle_no_norm="COMBO001"
    )
    insert_in_progress(
        raw_conn, direction="OUTBOUND", partner_name="Combo Partner", vehicle_no_norm="COMBO002"
    )

    resp = api_client.get(BASE_URL, params={"direction": "INBOUND", "partner_name": "combo"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["direction"] == "INBOUND"


# ---------------------------------------------------------------------------
# 날짜 범위: started_from 포함, started_to 미포함
# ---------------------------------------------------------------------------


def test_date_range_from_inclusive_to_exclusive(raw_conn, api_client):
    base = datetime(2026, 1, 10, 3, 0, 0, tzinfo=timezone.utc)
    id_at_from, _ = insert_in_progress(raw_conn, vehicle_no_norm="DATE0001")
    _set_gross_weighed_at(raw_conn, id_at_from, base)
    id_at_to, _ = insert_in_progress(raw_conn, vehicle_no_norm="DATE0002")
    _set_gross_weighed_at(raw_conn, id_at_to, base + timedelta(hours=1))

    resp = api_client.get(
        BASE_URL,
        params={"started_from": _iso(base), "started_to": _iso(base + timedelta(hours=1))},
    )
    ids = {item["id"] for item in resp.json()["items"]}
    assert id_at_from in ids
    assert id_at_to not in ids


def test_started_from_equal_started_to_is_422(api_client):
    ts = _iso(datetime(2026, 1, 1, tzinfo=timezone.utc))
    resp = api_client.get(BASE_URL, params={"started_from": ts, "started_to": ts})
    assert resp.status_code == 422


def test_started_from_after_started_to_is_422(api_client):
    resp = api_client.get(
        BASE_URL,
        params={
            "started_from": _iso(datetime(2026, 1, 2, tzinfo=timezone.utc)),
            "started_to": _iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        },
    )
    assert resp.status_code == 422


def test_naive_datetime_is_422(api_client):
    resp = api_client.get(BASE_URL, params={"started_from": "2026-01-01T00:00:00"})
    assert resp.status_code == 422


def test_z_and_offset_timezone_give_same_result(raw_conn, api_client):
    # KST 09:00 == UTC 00:00
    record_id, _ = insert_in_progress(raw_conn, vehicle_no_norm="TZ00001")
    _set_gross_weighed_at(raw_conn, record_id, datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc))

    resp_z = api_client.get(
        BASE_URL,
        params={"started_from": "2026-03-01T00:00:00Z", "started_to": "2026-03-01T00:00:01Z"},
    )
    resp_offset = api_client.get(
        BASE_URL,
        params={
            "started_from": "2026-03-01T09:00:00+09:00",
            "started_to": "2026-03-01T09:00:01+09:00",
        },
    )
    assert resp_z.json()["total"] == 1
    assert resp_offset.json()["total"] == 1
    assert resp_z.json()["items"][0]["id"] == resp_offset.json()["items"][0]["id"] == record_id


# ---------------------------------------------------------------------------
# 정렬
# ---------------------------------------------------------------------------


def test_default_sort_desc_with_id_tiebreaker(raw_conn, api_client):
    same_time = datetime(2026, 2, 1, tzinfo=timezone.utc)
    id1, _ = insert_in_progress(raw_conn, vehicle_no_norm="SORT0001")
    _set_gross_weighed_at(raw_conn, id1, same_time)
    id2, _ = insert_in_progress(raw_conn, vehicle_no_norm="SORT0002")
    _set_gross_weighed_at(raw_conn, id2, same_time)

    resp = api_client.get(BASE_URL)
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == sorted([id1, id2], reverse=True)


def test_sort_dir_asc_applies_to_both_fields(raw_conn, api_client):
    same_time = datetime(2026, 2, 1, tzinfo=timezone.utc)
    id1, _ = insert_in_progress(raw_conn, vehicle_no_norm="SORT1001")
    _set_gross_weighed_at(raw_conn, id1, same_time)
    id2, _ = insert_in_progress(raw_conn, vehicle_no_norm="SORT1002")
    _set_gross_weighed_at(raw_conn, id2, same_time)

    resp = api_client.get(BASE_URL, params={"sort_dir": "asc"})
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == sorted([id1, id2])


# ---------------------------------------------------------------------------
# 상세 조회
# ---------------------------------------------------------------------------


def test_get_by_id_success_includes_cancel_fields_as_null(raw_conn, api_client):
    record_id, _ = insert_in_progress(raw_conn, vehicle_no_norm="DET0001")

    resp = api_client.get(f"{BASE_URL}/{record_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == record_id
    assert body["cancelled_at"] is None
    assert body["cancelled_by"] is None
    assert body["cancel_reason"] is None


def test_get_by_id_not_found(api_client):
    resp = api_client.get(f"{BASE_URL}/999999999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "계근기록을 찾을 수 없습니다"}


def test_get_by_id_invalid_format(api_client):
    resp = api_client.get(f"{BASE_URL}/not-a-number")
    assert resp.status_code == 422


def test_get_by_ticket_no_success(raw_conn, api_client):
    record_id, ticket_no = insert_in_progress(raw_conn, vehicle_no_norm="TKD0001")

    resp = api_client.get(f"{BASE_URL}/ticket/{ticket_no}")
    assert resp.status_code == 200
    assert resp.json()["id"] == record_id


def test_get_by_ticket_no_not_found(api_client):
    resp = api_client.get(f"{BASE_URL}/ticket/20000101-9999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "계근번호에 해당하는 기록을 찾을 수 없습니다"}


# ---------------------------------------------------------------------------
# 취소 기록
# ---------------------------------------------------------------------------


def test_canceled_record_included_in_default_list_without_cancel_fields(raw_conn, api_client):
    record_id, _ = insert_in_progress(raw_conn, vehicle_no_norm="CAN0001")
    cancel(raw_conn, record_id, cancelled_by="tester", cancel_reason="mistake")

    resp = api_client.get(BASE_URL)
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["status"] == "CANCELED"
    assert "cancelled_at" not in item
    assert "cancelled_by" not in item
    assert "cancel_reason" not in item


def test_status_filter_isolates_canceled(raw_conn, api_client):
    insert_in_progress(raw_conn, vehicle_no_norm="CAN1001")
    id2, _ = insert_in_progress(raw_conn, vehicle_no_norm="CAN1002")
    cancel(raw_conn, id2)

    resp = api_client.get(BASE_URL, params={"status": "CANCELED"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == id2


def test_canceled_record_detail_includes_cancel_fields(raw_conn, api_client):
    record_id, _ = insert_in_progress(raw_conn, vehicle_no_norm="CAN2001")
    cancel(raw_conn, record_id, cancelled_by="tester", cancel_reason="mistake")

    resp = api_client.get(f"{BASE_URL}/{record_id}")
    body = resp.json()
    assert body["status"] == "CANCELED"
    assert body["cancelled_by"] == "tester"
    assert body["cancel_reason"] == "mistake"
    assert body["cancelled_at"] is not None


# ---------------------------------------------------------------------------
# UTC 직렬화 / 내부정보 비노출
# ---------------------------------------------------------------------------


def test_response_datetime_has_utc_offset(raw_conn, api_client):
    record_id, _ = insert_in_progress(raw_conn, vehicle_no_norm="UTC0001")

    resp = api_client.get(f"{BASE_URL}/{record_id}")
    body = resp.json()
    assert body["created_at"].endswith("+00:00")
    assert body["updated_at"].endswith("+00:00")
    assert body["first_weighed_at"].endswith("+00:00")


_FORBIDDEN_SNIPPETS = [
    "127.0.0.1",
    "5432",
    "psycopg",
    "sqlalchemy",
    "app:app",
    "traceback",
]


def test_404_and_422_do_not_leak_internal_details(api_client):
    resp_404 = api_client.get(f"{BASE_URL}/999999999")
    resp_422 = api_client.get(BASE_URL, params={"page_size": 999})

    for resp in (resp_404, resp_422):
        text = resp.text.lower()
        for snippet in _FORBIDDEN_SNIPPETS:
            assert snippet not in text
