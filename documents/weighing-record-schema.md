# 1단계: 계근기록 데이터 모델 설계

이 문서는 `backend/alembic/versions/20260928_2043_c371b9220ac7_create_weighing_tables.py`가
구현한 1단계 스키마를 설명한다. 코드가 실제 동작이고, 이 문서는 "왜 이렇게
설계했는지"를 나중에 참고하기 위한 요약이다. 세부 SQL은 마이그레이션 파일
자체와 그 안의 주석을 우선 참고한다.

## 1. 테이블과 주요 필드

### `weighing_records` — 계근기록 현재값 (거래 1건 = 1행)

총중량(gross)과 공차(tare)를 한 행에 함께 둔다. 입고는 총중량이 먼저,
출고는 공차가 먼저 들어올 수 있어 "1차/2차" 대신 역할 이름을 쓰고, 어느
쪽이 먼저인지는 각자의 계근 시각으로 구분한다. 이 순서 자체는 DB가
강제하지 않는다.

| 필드 | 설명 |
|---|---|
| `id` | 내부 PK (BIGINT, 자동 증가) |
| `ticket_no` | 계근번호. DB 트리거가 자동 채번(아래 3번 참고), 전역 UNIQUE |
| `direction` | `INBOUND` / `OUTBOUND` |
| `status` | `IN_PROGRESS` / `COMPLETED` / `CANCELED` |
| `partner_name`, `item_name`, `operator_name` | 거래처·품목·담당자 (마스터 테이블 없이 스냅샷 문자열) |
| `vehicle_no_raw` / `vehicle_no_norm` | 입력 원문 / 정규화 값(검색·중복 검사용) |
| `gross_*`, `tare_*` | 각각 중량·계근시각·출처(`SCALE`/`MANUAL`/`IMPORT`)·수동입력 작업자·사유 |
| `net_weight_kg` | **DB 생성 컬럼**: `gross_weight_kg - tare_weight_kg` |
| `first_weighed_at` | **DB 생성 컬럼**: `LEAST(gross_weighed_at, tare_weighed_at)`. 기간 조회 기준 시각 |
| `cancelled_at/by/reason` | 취소 시각·작업자·사유 |
| `created_at` / `updated_at` | 시스템 등록·마지막 변경 시각 |

실중량과 기준 시각을 서비스 코드가 아니라 DB가 계산하므로, 원본값(총중량·
공차)과 계산 결과가 어긋날 수 없다.

### `weighing_record_history` — 추가 전용 변경 이력

`weighing_records`의 모든 INSERT/UPDATE를 트리거가 자동으로 기록한다
(`event_type`: `CREATED` / `UPDATED`, `before_data`/`after_data`는 JSONB
스냅샷). 작업자·사유·승인자 컬럼은 아직 없다 — 인증·승인 서비스가 붙는
단계에서 함께 추가한다.

### `weighing_ticket_sequences` — 날짜별 채번 카운터

`(ticket_date, last_seq)` 한 행씩. 계근번호 채번 함수가 원자적으로
증가시킨다(아래 3번).

## 2. 상태 전이

```
(신규 등록) --------> IN_PROGRESS   (총중량 또는 공차 중 하나만 있음)
IN_PROGRESS --------> COMPLETED     (나머지 계근값이 들어오면 자동)
IN_PROGRESS --------> CANCELED      (사유·작업자 필수)
COMPLETED   --------> CANCELED      (사유·작업자 필수)
CANCELED    --------> (불가, 종결 상태)
```

- `COMPLETED`는 실무상 "계근 확정" 상태로 쓰며, 향후 재고 반영은
  `COMPLETED` 기록만 대상으로 한다(재고 로직 자체는 이번 단계에 없음).
- `CANCELED`로의 **최초 전환**은 허용되지만, 이미 `CANCELED`인 행에 대한
  이후 모든 UPDATE는 트리거가 차단한다(4번 참고). 잘못 취소한 건은 복구
  하지 않고 새 기록으로 다시 등록한다.

## 3. 계근번호 채번 규칙

형식은 `YYYYMMDD-0001`이며 날짜별로 `0001`부터 다시 시작한다.

- **날짜 기준**: 저장되는 모든 timestamp는 UTC(`timestamptz`)다. 계근번호의
  `YYYYMMDD`만 예외로 **Asia/Seoul 달력 날짜**를 기준으로 계산한다(공장이
  한국에 있고, 계근번호는 "그날 업무"를 가리키는 사람이 읽는 식별자이기
  때문). 저장 형식 자체를 바꾸는 것이 아니라, 채번 시점에 `now() AT TIME
  ZONE 'Asia/Seoul'`로 변환한 뒤 날짜만 취하는 것이다.
- **동시성 안전성**: `next_ticket_no()` 함수가 `INSERT ... ON CONFLICT
  (ticket_date) DO UPDATE SET last_seq = last_seq + 1 RETURNING last_seq`로
  원자적으로 증가시킨다. "조회 후 +1" 방식이 아니므로 여러 요청이 동시에
  들어와도 같은 번호가 나가지 않는다(16개 동시 INSERT로 검증됨).
- **재사용 금지**: `ticket_no`는 상태와 무관하게 전역 UNIQUE다. 취소된
  건의 번호도 다시 쓸 수 없다.
- 계근번호는 애플리케이션이 값을 주지 않을 때만(`NULL`) 트리거가 채운다.

## 4. 중량 제약

- 중량은 kg 정수, 값이 있으면 **1 이상 200,000 이하**(`weighing_limits.py`의
  `MIN_WEIGHT_KG`/`MAX_WEIGHT_KG`, 향후 계근대 사양에 맞게 조정 가능).
- 총중량 ≥ 공차. 한쪽만 있으면 `IN_PROGRESS`(진행 중), 둘 다 있어야
  `COMPLETED`가 가능하다.
- `COMPLETED` 상태에서는 계산된 실중량도 **1kg 이상**이어야 한다(총중량과
  공차가 같은, 즉 실중량 0인 완료 건은 거부됨).
- 중량·계근시각·출처는 한 세트로 취급한다(하나만 있고 나머지가 없는 상태는
  불가). 출처가 `MANUAL`이면 작업자와 사유가 필수다.
- 등록 공차, 이물질·수분 공제는 이번 단계에서 다루지 않는다.

## 5. 취소 및 삭제 정책

- **물리적 삭제 금지**: `weighing_records`, `weighing_record_history` 모두
  DELETE가 트리거로 차단된다. `weighing_record_history`는 FK(`ON DELETE
  RESTRICT`)로도 한 번 더 보호된다.
- **취소 후 불변**: `weighing_records.status = 'CANCELED'`인 행은 이후
  어떤 UPDATE도 트리거가 차단한다(중량, 차량번호, 거래처, 품목 등 업무
  필드 전부 포함). 다만 정상 기록을 `CANCELED`로 **전환하는 최초 UPDATE**는
  막지 않는다 — 트리거는 `OLD.status`만 보고 판단하므로, 전환 시점에는
  아직 `CANCELED`가 아니기 때문이다.
- 막힌 UPDATE는 트랜잭션 전체가 실패하므로 이력에도 남지 않는다(원래
  실행되지 않은 것과 동일).

## 6. 변경 이력 정책

- `weighing_record_history`는 추가 전용이다(UPDATE/DELETE 모두 트리거가
  차단).
- `weighing_records`에 INSERT가 일어나면 `CREATED` 이벤트가, 업무상 실제
  값이 바뀌는 UPDATE가 일어나면 `UPDATED` 이벤트가 자동으로 쌓인다.
- `updated_at`만 바뀌고 다른 값은 그대로인 UPDATE(예: 같은 값으로 재저장)는
  이력 비교에서 `updated_at`을 제외하므로 이력에 쌓이지 않는다 — 노이즈
  방지를 위한 설계다.
- 작업자·사유·승인자는 이번 단계에 없다. 인증·승인 서비스가 붙을 때
  `weighing_record_history`에 함께 추가한다.

## 7. UTC 저장과 Asia/Seoul 채번 기준

- **저장**: 모든 timestamp 컬럼은 `timestamptz`이며 UTC로 저장·비교된다.
  화면 표시 시에만 현지 시간대로 변환한다(CLAUDE.md 원칙).
- **예외 — 계근번호 날짜만**: 3번에서 설명한 대로, 계근번호의 `YYYYMMDD`
  부분만 Asia/Seoul 달력 날짜를 기준으로 계산한다. 이는 저장 형식의
  예외가 아니라 "사람이 읽는 식별자를 만들 때 어떤 시간대의 하루를
  기준으로 자를 것인가"에 대한 업무 규칙이다.

## 8. 이번 단계에서 제외한 것

- 조회·등록 **API 엔드포인트** (2단계 예정)
- **사용자 인증·역할별 권한**, 승인자 검증
- 중량·정보 **정정(승인) 서비스**(`correct_weight` 등) — 스키마(이력 테이블,
  CHECK, 삭제 금지)는 이미 갖춰 두었지만 실행 로직은 없음
- **유사·중복 계근 탐지**(같은 차량·비슷한 시각의 이중 등록 경고)
- **실제 재고 반영 로직** — `COMPLETED` 기록만 대상으로 한다는 정책만
  정해 두었다
- 거래처·품목·차량 **마스터 테이블**(지금은 스냅샷 문자열)
- 사진·계근표 첨부, 계근기(설비) 원본 페이로드 저장

## 9. 다음 단계에서 추가할 항목

- ~~계근기록 조회 API~~ — **2단계에서 완료.** [documents/weighing-query-api.md](weighing-query-api.md) 참고.
- 등록·정정·취소 서비스 계층과 최소한의 인증
- `weighing_record_history`에 작업자·사유·승인자 컬럼 추가
- 거래처/품목/차량 마스터 테이블과 점진적 이관(문자열 → `*_id` FK)
- 계근기(설비) 연동을 위한 `scale_readings`(원본 페이로드, 불변) 테이블
- 재고 원장(`inventory_transactions`)과 `COMPLETED` 기록 연결
