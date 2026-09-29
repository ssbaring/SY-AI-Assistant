# 2단계: 계근기록 조회 API

이 문서는 `feat: 계근기록 조회 API 추가`(커밋 `090f287`, PR #3, main 병합 커밋
`614f197`)로 구현된 조회 전용 API를 설명한다. 코드는
`backend/app/api/v1/weighing_records.py`, `backend/app/schemas/weighing_record.py`,
`backend/app/services/weighing_records_query.py`에 있다. 등록·수정·취소 API,
인증, 재고 반영은 이번 단계에 포함되지 않았다.

## 1. 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/v1/weighing-records` | 목록 조회 |
| GET | `/api/v1/weighing-records/ticket/{ticket_no}` | 계근번호로 상세 조회 |
| GET | `/api/v1/weighing-records/{record_id}` | ID(정수)로 상세 조회 |

라우트 등록 순서는 목록 → `/ticket/{ticket_no}` → `/{record_id}`이며(
`weighing_records.py`), `record_id`는 `int` 타입으로 선언되어 정수가
아닌 값은 FastAPI가 자동으로 422를 반환한다.

## 2. 응답 스키마

- `WeighingRecordOut`(목록 항목): `id`, `ticket_no`, `direction`, `status`,
  `vehicle_no`, `partner_name`, `item_name`, `operator_name`,
  `gross_weight_kg`, `tare_weight_kg`, `net_weight_kg`, `first_weighed_at`,
  `created_at`, `updated_at`.
- `WeighingRecordDetail`(단건 상세, `WeighingRecordOut`을 상속): 위 필드에
  `cancelled_at`, `cancelled_by`, `cancel_reason`을 추가한다.
- `vehicle_no`는 모델의 `vehicle_no_raw`를 그대로 노출한다. `vehicle_no_norm`은
  검색에만 쓰이고 응답에는 나오지 않는다(`weighing_records_query.py`의
  `_common_fields`).
- 취소 관련 3개 필드는 **목록 응답에는 포함되지 않고 상세 응답에만** 있다.
- 목록 응답 형식: `items`, `page`, `page_size`, `total`, `total_pages`.

## 3. 필터

`WeighingRecordListParams`(`app/schemas/weighing_record.py`)에 정의된
쿼리 파라미터:

| 파라미터 | 정책 |
|---|---|
| `direction` | `INBOUND`/`OUTBOUND` 완전일치 |
| `status` | `IN_PROGRESS`/`COMPLETED`/`CANCELED` 완전일치 |
| `vehicle_number` | 완전일치(아래 4절 정규화 참고) |
| `partner_name` | 부분일치, 대소문자 무시(`ILIKE '%값%'`) |
| `material_name` | 부분일치, 대소문자 무시(`item_name` 컬럼에 `ILIKE`) |
| `ticket_no` | 완전일치 |
| `started_from`, `started_to` | 5절 참고 |

문자열 필터(`vehicle_number`/`partner_name`/`material_name`/`ticket_no`)가
빈 문자열이거나 공백만 있으면 422다(`_reject_blank`).

## 4. 차량번호 정규화

`app/services/vehicle_no.py`의 `normalize_vehicle_no()`가 공백과 하이픈을
제거하고 대문자로 바꾼다. `vehicle_number` 필터는 이 함수로 정규화한 값을
DB의 `vehicle_no_norm` 컬럼과 완전일치로 비교한다. 정규화 후 결과가 빈
문자열이면 422다. 부분일치는 지원하지 않는다.

## 5. 날짜 범위 정책

- 조회 조건: `started_from <= first_weighed_at < started_to`
  (`started_from` 포함, `started_to` 미포함).
- `started_from`/`started_to`는 **타임존 정보가 있는 값만** 허용한다
  (`Z` 또는 `+09:00` 등). 타임존이 없는 값(naive datetime)은 422다.
- 통과한 값은 내부적으로 UTC로 변환한 뒤 쿼리에 쓴다(`_require_timezone_and_convert_to_utc`).
- `started_from`이 `started_to`보다 늦거나 같으면 422다(같은 시각도 허용하지
  않음, `_validate_date_range`).

## 6. 정렬과 페이지네이션

- 정렬 기준은 항상 `first_weighed_at`이고, 보조 정렬로 `id`를 더한다.
  `sort_dir=desc`(기본값)면 둘 다 내림차순, `sort_dir=asc`면 둘 다
  오름차순이다(`_apply_sort`). `sort_by`는 만들지 않았다.
- `page` 기본값 1, `page_size` 기본값 20·최대 100. 범위를 벗어나면 422다.
- `total`은 같은 필터 조건의 `COUNT` 쿼리로 구하고, `total_pages`는
  `ceil(total / page_size)`이며 `total=0`이면 `0`이다.

## 7. 취소 기록 조회 정책

- `CANCELED` 상태도 기본 목록·상세 조회에 포함된다(별도 제외 없음).
- `status=CANCELED`로 취소 기록만 조회할 수 있다.
- 취소 사유(`cancel_reason`)와 취소 시각·작업자(`cancelled_at`/`cancelled_by`)는
  상세 응답에만 포함되고 목록 응답에는 없다(2절 참고).

## 8. UTC 응답 직렬화

응답의 `first_weighed_at`/`created_at`/`updated_at`/`cancelled_at`은 UTC로
변환한 뒤 `+00:00` 오프셋을 포함한 ISO 8601 문자열로 직렬화한다
(`WeighingRecordOut`/`WeighingRecordDetail`의 `field_serializer`).

## 9. DB 스키마 변경 여부

**없음.** 기존 1단계 마이그레이션(`c371b9220ac7`)의 컬럼과 인덱스만으로
구현했고, 새 마이그레이션을 추가하지 않았다.

## 10. 인덱스 관련 보류 사항

`partner_name`/`material_name`의 `ILIKE '%값%'` 부분일치는 기존 btree
인덱스(`ix_weighing_records_partner_first` 등)를 타지 않는다. 현재
MVP 데이터 규모에서는 이 상태를 유지하기로 했고, `pg_trgm` 확장과 GIN
인덱스 추가는 이번 단계에서 하지 않았다. 실제 데이터가 늘어난 뒤
`EXPLAIN ANALYZE`와 응답 시간을 측정해 별도 성능 개선 브랜치에서
검토하기로 했다.

## 11. 테스트

`backend/tests/weighing/test_weighing_query_api.py`에 33개 테스트를
추가했다(목록 페이지네이션, 필터 단독·조합, 날짜 경계와 타임존 검증,
정렬, ID/계근번호 상세 조회, 취소 기록 노출 정책, UTC 직렬화, 404/422
응답의 내부정보 비노출 등). 기존 40개(0~1단계)와 합쳐 전체 pytest는
73개이며, 이 시점 기준 73개 모두 통과했다.
