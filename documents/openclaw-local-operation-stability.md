# 5단계: 로컬 운용 안정화와 비용·권한 점검

이 문서는 4단계까지 연결한 OpenClaw 비서 구성을 한 대의 개발 PC에서 안정적으로 운용하기 위해
바꾼 사용자 설정, 운용 절차, 검증 결과와 남은 한계를 정리한다. 3단계는
[openclaw-factory-tools.md](openclaw-factory-tools.md), 4단계는
[openclaw-coordinator-delegation.md](openclaw-coordinator-delegation.md)에 있다.

## 0. 현재 상태

범위는 **한 대의 개발 PC와 비어 있는 개발 DB에서의 로컬 운용**이다. 운영 배포나 전체 보안 검증이
아니다. 변경은 모두 사용자 OpenClaw 설정(Git에 포함되지 않음)과 이 문서·README이며, backend,
MCP 서버 코드, DB, 인증 설정, coordinator와 factory-operations의 도구 정책, 두 `AGENTS.md`는
바꾸지 않았다.

| 항목 | 상태 |
|---|---|
| 주기 작업 중지(heartbeat, Dreaming, 스킬 검토) | 적용, 실제 실행으로 확인(2026-10-10) |
| `session-memory` 훅 끄기 | 적용, 확인(2026-10-10, 10-15) |
| main 권한 좁히기(`minimal`, Code Mode 끔, `gateway` 차단) | 적용, 실제 실행으로 확인(2026-10-10) |
| main의 `presence` 도구 차단 | 적용, 실제 실행으로 확인(2026-10-15) |
| 위임 세션 정리 절차 | 1회 실행해 확인(2026-10-15) |
| 운용 순서(Docker·DB 확인부터 Gateway까지) | 정리함(2절) |

"확인"은 한 번의 실행에서 세션 기록, 실행 추적 기록, 상태 DB, Gateway 로그로 본 결과다. 같은
결과가 항상 나온다는 보증은 아니다.

## 1. 바꾼 사용자 설정

모두 `~\.openclaw\openclaw.json`이다. 단계마다 직전에 새 이름으로 백업했고, 각 단계에서
`config validate`와 백업 대비 차이를 확인했다(차이는 아래 값뿐이었다).

| 경로 | 값 | 목적 |
|---|---|---|
| `agents.defaults.heartbeat.every` | `"0m"` | 주기 heartbeat 끄기 |
| `plugins.entries.memory-core.config.dreaming.enabled` | `false` | Dreaming 끄기 |
| `skills.workshop.autonomous.mode` | `"off"` | 스킬 검토 작업 끄기 |
| `hooks.internal.entries.session-memory.enabled` | `false` | 초기화 때 대화 발췌를 기억 파일로 저장하는 훅 끄기 |
| `agents.entries.main.tools.profile` | `"minimal"` | main 도구 축소 |
| `agents.entries.main.tools.codeMode` | `false` | main의 `exec`, `wait` 경로 제거 |
| `agents.entries.main.tools.deny` | `["factory-api__*", "gateway", "presence"]` | 계근 도구(기존), 업데이트 요청, 접속 현황 읽기 차단 |

바꾸지 않은 것: 전역 `tools.profile`(`full`), main의 `session_status`, `tools.agentToAgent`,
coordinator와 factory-operations의 도구 정책, 인증 설정.

백업과 복사본(모두 `~\.openclaw` 아래):

| 이름 | 되돌리는 범위 |
|---|---|
| `openclaw.json.pre-stage5` | 5단계의 모든 설정 변경 |
| `openclaw.json.pre-stage5-hook` | 훅, main 권한, `presence` 차단 |
| `openclaw.json.pre-stage5-main-tools` | main 권한과 `presence` 차단 |
| `openclaw.json.pre-stage5-presence` | `presence` 차단만 |
| `stage5-memory-snapshot-20261009\` | 세 workspace의 `DREAMS.md`와 `memory/` 복사본(기록용) |

## 2. 운용 순서

Gateway를 켜기 전에 DB와 API가 정상인지 먼저 확인한다. 10월 10일에 PC를 다시 켠 뒤 Docker가
실행되지 않은 상태에서 조회가 HTTP 500으로 실패했고(6.2절), 10월 15일에는 8000번 포트를 다른
프로그램이 쓰고 있어 FastAPI를 바로 띄우지 못했다.

1. **Docker와 DB 확인.** Docker Desktop을 실행하고 컨테이너가 떠 있는지 본다.

   ```powershell
   docker ps --filter name=sy_holdings_dev_db
   ```

   목록에 없으면 `docker compose -f infrastructure/docker-compose.yml up -d`로 올린다.

2. **8000번 포트 확인.** 다른 프로그램이 쓰고 있지 않은지 본다.

   ```powershell
   Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
   ```

   결과가 있으면 `OwningProcess`의 프로그램을 확인하고, 이 저장소의 FastAPI가 아니면 그 프로그램을
   종료한 뒤 진행한다.

3. **FastAPI 실행** (창 1).

   ```powershell
   cd <저장소>\backend
   .venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

4. **`/health/db` 확인** (다른 창).

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/health/db
   ```

   정상 응답이 아니면 Gateway를 켜지 않는다.

5. **키 입력과 Gateway 실행** (창 2, 키가 있는 PowerShell).

   ```powershell
   [bool]$env:<키 환경변수 이름>
   openclaw gateway run
   ```

6. 끝나면 Gateway와 FastAPI를 Ctrl+C로 종료한다.

## 3. 주기 작업

### 3.1 적용 전

OpenClaw가 자동으로 등록한 예약 작업이 5개 있었다. 사용자가 만든 것은 없다.

| 작업 | 용도(포함 문서) | 적용 전 관찰 |
|---|---|---|
| Heartbeat (main, 30분) | 비서를 주기적으로 깨워 확인할 일을 보게 함 | 22회 발동, 실제 실행 1회(약 2만 토큰). 나머지는 보낼 경로가 없어 건너뜀 |
| Memory Dreaming Promotion (매일 03:00) | 세션 대화를 읽어 모델로 기억·일기를 씀 | 3회 실행. 세 workspace에 시험 대화 내용(계근번호 등)이 든 파일을 남김 |
| Skill collection review (비서별, 7일) | 스킬 모음 검토 | 실행 이력 없음. 작업에 파일 쓰기·명령 실행 도구가 지정돼 있었음 |

### 3.2 적용 후 (2026-10-10 확인)

- 남은 작업 4개(`heartbeat-main`, `skill-collection-review-main`, `-coordinator`,
  `-factory-operations`)가 모두 비활성이다. Dreaming 작업은 Gateway 시작 때 목록에서 삭제됐다.
- Gateway 시작 직후, 예정 시각이 지나 있던 작업 2개가 "건너뜀"으로 기록됐고 비서 실행은 생기지 않았다.
- 시험이 끝난 기준 시각부터 약 37분 동안 실행 영수증과 비서 실행이 없었다. 10월 15일 실행에서도
  예약 작업 실행은 없었다.
- **증명 범위:** 관찰한 시간 동안 heartbeat가 발동하지 않았다는 것까지다. 일간·주간 작업은 삭제·비활성
  표시까지 확인했고, 앞으로 실행되지 않는다는 것은 장기 운용 기록으로 확인할 항목이다.

## 4. main 비서의 권한

| 구분 | 내용 |
|---|---|
| 적용 후 남은 도구(10-10 확인) | `session_status`, `presence`, Tool Search 제어 도구(`tool_call`, `tool_describe`, `tool_search`) |
| 빠진 도구(10-10 확인) | `exec`, `wait`, 파일 도구, 세션 전송·생성·기록 조회, 기억 검색, `gateway`, 계근 도구 |
| `presence` | 포함 문서의 `minimal` 목록에 없던 도구가 남아 있었다. 접속한 사람·기기·활동을 읽는다. 10-15에 `deny`에 추가했다 |
| `presence` 차단 후(10-15 확인) | 정책 적용 후 남은 업무 도구는 `session_status` 1개. 모델에 직접 전달된 것은 Tool Search 제어 도구 3개(`tool_call`, `tool_describe`, `tool_search`)이고, 이것으로 호출할 수 있는 대상이 `session_status`뿐이다. 로그의 도구 목록 수 1개 |
| main의 역할 | 시스템 소유자, 음성 대화 대상. 사용자 창구는 coordinator다 |

설정으로 강제되는 것은 도구 프로필, `codeMode`, `deny`, 에이전트 간 허용 목록이다. 운영자가 CLI로
main과 직접 대화하는 것, main에게 업무를 시키지 않는 것은 운영 수준이다. main의 workspace 지침은
OpenClaw 기본 템플릿 그대로다. main의 시스템 프롬프트는 실행 추적 기록에 가려진 채 저장돼 있어
기억 내용이 들어가는지 확인하지 못했다.

`presence` 차단 확인(2026-10-15, 시험 요청 1건, 세션 `stage5-main-2`): 답은 "OK"였고 도구 호출은 없었다.
정책 적용 후 남은 업무 도구는 `session_status` 1개였고 `presence`, `exec`, `wait`, 파일·세션·기억 검색 도구,
`gateway`, 계근 도구는 없었다. `codeMode`는 설정값이 `false`이고, 실행 기록에서 `exec`·`wait`가 노출되지 않은 것으로
판단했다(실행 기록에 Code Mode 상태를 직접 적은 항목은 없다). 한 번의 실행에서 확인한 결과다.

## 5. 위임 세션 정리

공유 위임 세션(`agent:factory-operations:delegation`)은 요청이 쌓일수록 실행당 입력이 커진다.
기록상 실행당 `usage.total`은 10월 8일 약 4.9만에서 10월 10일 약 7.1만까지 늘었다.

### 5.1 초기화 전 확인

Gateway를 정상 종료한 상태에서 아래 6개를 읽기 전용으로 확인한다. 하나라도 통과하지 못하거나 값의
의미가 불분명하면 초기화를 보류한다.

| # | 확인 | 통과 조건 |
|---|---|---|
| 1 | Gateway 종료 | 포트에 리스너가 없고 마지막 종료가 `clean_stop` |
| 2 | 세션 상태 | coordinator와 factory-operations의 세션이 모두 `done` |
| 3 | 끝나지 않은 실행 | 0개 |
| 4 | 대기 입력 | 상태별로 본다. 초기화 대상 비서에 어떤 상태의 입력도 없음 |
| 5 | 미완료 위임 | 모든 `sessions_send` 결과가 `ok`이거나, `accepted`인 건마다 뒤따르는 응답이 도착해 있음 |
| 6 | 건별 대응 | 위임마다 `runId`, 출처 세션, 시각으로 대응하는 대상 실행이 하나 있고 끝나 있음 |

- 늦은 답의 전달 창(대기 시간 뒤 최대 60초)은 대상 실행의 종료를 뜻하지 않는다. 시간 경과만으로
  판단하지 않는다.
- 날짜가 바뀌어도 이 확인은 생략하지 않는다.
- 확인 뒤 어떤 요청도 보내지 않은 동안만 유효하다. Gateway를 켠 직후 다른 요청보다 먼저 초기화한다.
- 2~4번 값의 의미는 실행 중·대기 중 상태를 관찰한 적이 없어 [미검증]이다.
- main에는 2026-10-06자 `interrupted` 상태의 입력 3행이 남아 있다. 초기화 대상과 무관하다.

### 5.2 초기화 명령과 성공 기준

```powershell
openclaw agent --agent factory-operations --session-key delegation --message "/new"
```

성공 기준은 다음 세 가지다. **세션 ID 변경은 기준이 아니다.**

| 기준 | 확인할 기록 |
|---|---|
| `reset` 사건 기록 | 위임 세션의 대화 기록에 이유 `new`인 `reset` 사건이 남는다 |
| 다음 실행의 이전 메시지 0개 | 초기화 뒤 첫 실행에서 모델에 전달된 이전 메시지 수가 0이다 |
| 세션 키 유지 | `agent:factory-operations:delegation`이 그대로 있고 `sessions_send`가 도달한다 |

이 설치에서 CLI로 보낸 `/new`는 새 세션 ID를 만들지 않고, 같은 세션 ID 안에서 대화 맥락만 새로
시작했다. **기존 대화 기록은 삭제되지 않고 같은 세션 기록 안에 남아 있다.** 포함 문서의 "현재
세션을 보관하고 새 세션을 시작한다"는 설명과 이 동작의 관계는 확인하지 못했다.

### 5.3 실행 결과 (2026-10-15)

| 항목 | 결과 |
|---|---|
| 초기화 전 확인 6개 | 모두 통과(위임 19건이 대상 실행 19개와 건별 대응) |
| 화면 | "✅ New session started." |
| `/new`의 모델 호출 | 없음(실행 추적 기록과 로그에 해당 시각의 실행·요청 없음) |
| `reset` 사건 | 기록됨 |
| 다음 실행의 이전 메시지 | 0개(초기화 전 마지막 4회는 매번 64개) |
| 세션 키 | 유지. 세션 ID는 이전과 같음 |
| 후속 조회 1건 | 위임 1회, API 조회 1회, `total: 0`. 요청 ID와 요청 세션이 위임 메시지, 플랫폼 출처, 답, 최종 전달에서 일치 |
| 이전 대화의 흔적, 기억 파일 | 프롬프트와 메시지에 이전 요청의 흔적 없음. 기억 파일 생성·변경 없음 |
| 초기화 뒤 미완료 실행, 대기 위임 | 없음 |

입력 규모:

| 실행 | 기록상의 `usage.total` |
|---|---|
| 초기화 전 마지막 실행(10-10) | 71,294 |
| 초기화 후 첫 실행(10-15) | 43,318 |

- 이 값은 실행 추적 기록의 실행 완료 사건에 남은 `usage.total`이다. 입력뿐 아니라 출력, 캐시 읽기,
  캐시 쓰기를 포함한다.
- 이 값이 실행 안의 여러 모델 요청을 합한 것인지, 마지막 요청의 값인지는 **확인하지 못했다.**
  같은 필드끼리 비교한 것이다.
- 한 번의 비교다. 감소 폭을 일반적인 비용 절감률로 보지 않는다. 요청이 쌓이면 다시 늘어난다.

함께 관찰된 것(각 1회 관찰, 일반화하지 않는다):

- 초기화 뒤 첫 실행에서 factory-operations가 도구 검색(`tool_search`, `tool_describe`)을 다시 한 뒤
  조회 도구를 불렀다. 초기화 전에는 바로 조회 도구를 불렀다.
- 같은 Gateway 실행에서 보조 모델(`claude-haiku-4-5`) 요청이 1회 있었다. coordinator의 새 세션이
  시작된 직후였다. 같은 날 main 확인 시험에서도 새 세션이 시작된 직후 1회 관찰됐다. 포함 문서상 보조
  모델은 세션 제목 같은 짧은 내부 작업에 쓰이며, 이번 용도와 전달 내용은 확인하지 못했다. 10월 10일의
  두 시험에서는 관찰되지 않았다.
- Gateway 로그는 모든 모델 요청을 남기지 않는 것으로 보인다(coordinator의 첫 요청 줄이 없었다).
  로그만으로 요청 수를 세지 않는다.

### 5.4 요청 식별자와의 관계

- 요청 식별자 (요청 세션, 요청 ID)는 coordinator 세션에서 나온다. 위임 세션을 초기화해도
  식별자는 재사용되지 않는다.
- coordinator 세션을 초기화하면 같은 세션 키에서 순번이 001부터 다시 시작한다. coordinator 세션은
  5.1절의 확인을 한 뒤, 한국 시간으로 날짜가 바뀐 다음 첫 요청 전에만 초기화한다. 이 규칙은 운영
  수준이다.

## 6. 조회 검증

### 6.1 정상 조회

| 날짜 | 결과 |
|---|---|
| 2026-10-10 14:21 | 위임 1회, 조회 1회, `total: 0`. FastAPI 200(사용자 보고) |
| 2026-10-15 05:20 | 위임 세션 초기화 뒤 같은 결과(5.3절) |

5단계 설정 적용 뒤에도 coordinator와 factory-operations의 연결, 도구 제한(1개, 3개), 식별자 쌍
일치가 유지됐다.

### 6.2 HTTP 500 오류의 전달

2026-10-10 13:02의 조회가 HTTP 500으로 실패했다.

- factory-operations는 MCP 도구의 "API 요청을 처리하지 못했습니다. (HTTP 500)"을 그대로 보고했고,
  0건으로 처리하거나 건수를 만들지 않았다. 입력 오류가 아니라는 이유로 재조회하지 않았다.
- coordinator도 오류를 그대로 전달했고 다시 위임하지 않았다.
- 4단계에서 시험하지 않았던 오류 경로에서도 재조회 금지가 1회 지켜졌다.

원인은 **추정**이다. 그날 PC를 다시 켠 뒤 Docker Desktop이 실행되지 않아 DB가 떠 있지 않았고,
계근 조회 경로에 DB 오류 처리가 없어 FastAPI가 500을 돌려준 것으로 본다. DB를 확인한 뒤의
재시험은 정상이었다. 13:02 시점에 DB가 내려가 있었다는 직접 기록과 backend의 오류 내용은 없어
확정하지 않는다.

## 7. 기존 기억 파일

- Dreaming을 꺼서 새 파일은 생기지 않는다. 이미 생긴 `DREAMS.md`와 `memory/dreaming/` 파일은 원본을
  그대로 두고 workspace 밖에 복사본을 만들었다(1절).
- coordinator와 factory-operations: 실제 실행의 프롬프트에 주입된 파일은 `AGENTS.md`, `SOUL.md`,
  `IDENTITY.md`, `USER.md`뿐이었고 기억 색인도 비어 있다. 기존 파일이 쓰이지 않는다(확인된 범위).
- main: 기억 색인에 dreaming 파일이 들어 있다. main에서 기억 검색 도구가 빠져 검색 경로는 닫혔다.
  프롬프트에 기억 내용이 들어가는지는 확인하지 못했다.
- `MEMORY.md`는 세 workspace 모두 없다.
- 원본의 정리 여부는 운영 데이터를 넣기 전에 다시 정한다. 지금 들어 있는 것은 시험 내용뿐이다.

## 8. 복구

1. Gateway를 끈다.
2. 1절의 백업 중 되돌릴 범위에 맞는 것을 `openclaw.json`으로 복원한다.
3. `openclaw config validate`를 실행한다.
4. Gateway를 다시 시작한 뒤 `openclaw cron list --all`로 예약 작업 상태를 확인한다. 확인 전에는 켜졌다고
   단정하지 않는다.

설정 복원은 위임 세션 초기화를 되돌리지 않는다.

## 9. 남은 검증 한계와 후속

- main의 프롬프트에 기억 내용이 들어가는지.
- `/new`가 세션 ID를 바꾸지 않는 이유와 문서 설명의 관계.
- `usage.total`의 집계 방식.
- 보조 모델 호출의 용도, 발생 조건, 전달 내용.
- 일간·주간 작업이 앞으로 실행되지 않는다는 것(장기 운용 기록).
- 초기화 전 확인 2~4번 값의 의미(실행 중·대기 중 상태의 관찰).
- 위임 세션의 정리 시점(업무일 첫 요청 전 1회 또는 입력 규모 기준)은 운용하면서 정한다.
- 전역 `tools.profile`이 `full`이라 앞으로 프로필 없이 추가되는 비서는 넓은 권한을 받는다.
- 이전 HTTP 500의 원인 확정.
- API 인증, 집계 API, 실제 운영 데이터 검증은 이 단계의 범위가 아니다.
