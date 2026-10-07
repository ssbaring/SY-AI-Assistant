# SY Holdings AI Assistant

공장의 입출고·계근·생산·재고·수율·회계 업무를 통합 관리하는 AI 기반 업무 시스템. 업무 데이터와 계산은 프로그램이 처리하고, AI는 문서 판독·분류·검색·설명·명령 인터페이스를 담당한다. 상세 규칙은 [CLAUDE.md](CLAUDE.md) 참고.

## 현재 진행 단계

- **0단계 (부트스트랩) 완료** — 백엔드 프로젝트 뼈대(FastAPI, DB 연결), 로컬 개발 DB(Docker PostgreSQL), 헬스체크 테스트.
- **1단계 (계근기록 스키마·마이그레이션) 완료** — `weighing_records`/`weighing_record_history` 테이블, 계근번호 자동 채번, 삭제 금지·취소 후 불변 트리거. 자세한 내용은 [documents/weighing-record-schema.md](documents/weighing-record-schema.md) 참고.
- **2단계 (계근기록 조회 API) 완료** — `GET /api/v1/weighing-records` 목록/상세 조회 3종. 자세한 내용은 [documents/weighing-query-api.md](documents/weighing-query-api.md) 참고.
- 백엔드 pytest **73개 통과**.
- **3단계 (OpenClaw 도구 연결) — 로컬 읽기 전용 연결 MVP 검증 완료.** 한 대의 개발 PC와 비어 있는 개발 DB 기준이며, 회사 운영 배포나 전체 보안 검증이 끝난 것은 아니다. 자세한 내용은 [documents/openclaw-factory-tools.md](documents/openclaw-factory-tools.md) 참고.
  - 구현과 자동 검증: 조회 API 3종을 호출하는 stdio MCP 서버 `factory-api`(`openclaw/mcp-servers/factory-api/`). MCP 테스트 **182개 통과**(실제 테스트 DB 연동 검증 포함).
  - 실제 OpenClaw 연결: MCP 서버와 factory-operations 비서를 등록하고 실제 대화로 확인했다. 목록 조회 200·0건, 계근번호 상세 404, ID 상세 404, 합계 요청에는 기능 한계를 안내하고 임의로 계산하지 않음, main은 MCP 도구가 차단되고 업무 도구 호출 0회.
  - 도구 노출 방식: factory-operations는 Code Mode를 끄고 Tool Search를 유지한다. 조회 도구 3개는 직접 노출되지 않고 Tool Search를 거쳐 호출된다. 모델에 전달된 전체 도구 목록은 로그로 직접 확인하지 못했다.
  - **아직 확인하지 않은 것**: 실제 데이터가 있는 기록의 상세 응답을 비서가 정확히 전달하는지(성공 경로는 자동 연동 테스트로만 검증). 운영 데이터 준비 후 확인한다.
  - 후속 범위: API 인증, 집계 API, main 비서의 권한 축소, 보조 모델·주기 작업의 비용과 데이터 전달 범위 점검.
- **4단계 (coordinator 연결과 factory-operations 위임) — 로컬 읽기 전용 위임 MVP 검증 완료.** 한 대의 개발 PC와 비어 있는 개발 DB 기준이며, 회사 운영 배포나 전체 보안 검증이 끝난 것은 아니다. 자세한 내용은 [documents/openclaw-coordinator-delegation.md](documents/openclaw-coordinator-delegation.md) 참고.
  - 완료: 위임 방식 설계(`sessions_send`), 두 비서의 업무지침 수정, coordinator 등록과 도구 정책 적용(허용 도구는 `sessions_send` 1개, 계근 도구 차단, Code Mode 끔), 에이전트 간 허용 목록으로 main의 다른 비서 접근 차단. `config validate` 통과.
  - 1차 실제 검증(2026-10-08): 요청당 위임 1회와 전용 세션 사용, 자동 왕복 없음, 시간 초과 후 늦은 응답 처리, main의 교차 접근 정책 거부를 세션 기록으로 확인했다. 보완 4건(세션 간 요청 ID 중복, 공유 세션을 통한 과거 결과 혼입, 오류 후 자동 재조회, 0건·404에 대한 추측)이 나와 업무지침을 고쳤다.
  - 재검증(2026-10-08): 과거 결과 혼입, 0건·404 추측, 오류 후 자동 재조회는 재발하지 않았다. 요청 ID에 넣은 무작위 세션 표식은 세 세션이 같은 값을 골라 중복됐다. 그래서 요청을 (요청 세션, 요청 ID) 쌍으로 식별하도록 업무지침을 바꿨다. 요청 세션은 플랫폼이 가진 실제 세션 키다.
  - 식별 방식 변경 후 재검증(2026-10-08): 정상 경로 3건에서 coordinator가 보낸 식별자 쌍, factory-operations가 반환한 쌍, 사용자에게 전달한 쌍이 일치했다. 서로 다른 세션이 같은 요청 ID를 써도 요청 세션이 달라 쌍은 구분됐다. 요청당 위임 1회와 조회 1회, 도구 제한도 유지됐다.
  - **아직 확인하지 않은 것**: 요청 세션이나 요청 ID가 다른 응답을 거부하는지와 내부 대조가 항상 수행되는지(정상 경로에서는 발생하지 않음). coordinator의 금지 대상 전송은 지침 차단만 확인됐고 정책 거부는 미검증이다. 대기 중 위임 1건 규칙과 반복 호출 탐지의 효과도 미검증이다.
  - 한계: 요청 ID와 요청 세션의 전달·대조, 전용 세션 사용, 위임 횟수, 재시도·재조회 금지, 중복 전달 방지는 업무지침으로만 지켜지며 코드로 강제되지 않는다. 위임 전용 세션은 공유 세션이다. 같은 날짜에 세션을 초기화하면 요청 식별자가 다시 쓰일 수 있다.
  - 후속 과제: 공유 위임 세션은 요청이 쌓일수록 실행당 입력이 커진다. 세션 정리 주기와 방법은 아직 정하지 않았다.

## 사전 준비 상태 (이 PC 기준)

| 항목 | 상태 |
|---|---|
| Python 3.12.10 가상환경 (`backend/.venv`) | 구성 완료 |
| 운영·개발 의존성 (`requirements.txt`, `requirements-dev.txt`) | 구성 완료 (운영 의존성은 버전 고정) |
| Git | 설치됨 |
| Docker Desktop / PostgreSQL 16 컨테이너 | 구성 완료 (`127.0.0.1:5432`에서만 접속) |
| 백엔드 pytest | 73개 통과 |
| 0~2단계 | 완료 |
| MCP 서버 전용 가상환경 (`openclaw/mcp-servers/factory-api/.venv`, Python 3.12.10) | 구성 완료 (backend 환경과 분리) |
| OpenClaw 2026.9.8 (Windows 네이티브, Node.js v24.19.0) | 온보딩 완료. Gateway는 필요할 때 수동 실행(예약 작업 미설치). `factory-api` MCP 서버와 factory-operations 비서 등록됨 |
| OpenClaw 모델 인증 | Anthropic API 키를 환경변수 참조로 사용. 키는 Gateway를 실행하는 터미널에 매번 직접 입력(저장소·설정 평문에 없음) |

## PostgreSQL 준비 (Docker 방식, 사용자 직접 설치 필요)

관리자 권한이 필요해 자동화 도구가 아닌 **관리자 권한 PowerShell**에서 직접 실행해야 한다.

1. WSL2 설치 (재부팅이 필요할 수 있음):
   ```powershell
   wsl --install
   ```
2. 재부팅 후 Docker Desktop 설치:
   ```powershell
   winget install -e --id Docker.DockerDesktop
   ```
3. Docker Desktop 실행 후 정상 구동 확인:
   ```powershell
   docker info
   ```
4. 개발용 PostgreSQL 컨테이너 실행:
   ```powershell
   docker compose -f infrastructure/docker-compose.yml up -d
   ```

## 백엔드 로컬 실행 (DB 준비 후)

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env
.venv\Scripts\uvicorn app.main:app --reload
```

`.env`는 `.gitignore`에 포함되어 커밋되지 않는다. 실행 후 `http://127.0.0.1:8000/health`(서버 상태), `http://127.0.0.1:8000/health/db`(DB 연결 상태)로 확인한다.

`requirements.txt`는 서버 실행에 필요한 라이브러리, `requirements-dev.txt`는 여기에 테스트 도구(pytest, httpx2)를 더한 개발용 목록이다.

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 서버 상태 |
| GET | `/health/db` | DB 연결 상태 |
| GET | `/api/v1/weighing-records` | 계근기록 목록 조회(필터·정렬·페이지네이션) |
| GET | `/api/v1/weighing-records/ticket/{ticket_no}` | 계근번호로 상세 조회 |
| GET | `/api/v1/weighing-records/{record_id}` | ID로 상세 조회 |

조회 API의 필터·정렬·날짜 정책은 [documents/weighing-query-api.md](documents/weighing-query-api.md)에 정리되어 있다.

## 로컬 개발 전용 계정 안내

`docker-compose.yml`, `backend/.env.example`에 있는 `app / app` 계정은 **내 PC의 개발용 Docker DB에서만 쓰는 임시 계정**이다. 운영·공용 서버에는 절대 사용하지 않으며, 실제 비밀번호는 코드나 Git에 넣지 않는다.

## 테스트 실행

개발 DB(`sy_holdings_dev`)와 분리된 테스트 DB(`sy_holdings_dev_test`)를 사용한다. 테스트 DB는 첫 실행 때 자동으로 만들어지고, 개발 DB의 데이터는 읽거나 바꾸지 않는다. 실행 전에 PostgreSQL 컨테이너가 켜져 있어야 한다.

```powershell
cd backend
.venv\Scripts\python -m pytest
```

MCP 서버 테스트는 별도 가상환경에서 실행한다. 단위·프로토콜 테스트는 DB 없이 돌고, 연동 테스트(`test_e2e_backend.py`)만 backend 가상환경과 테스트 DB를 쓴다. PostgreSQL 컨테이너가 꺼져 있으면 연동 테스트는 건너뛰므로 `-rs`로 건너뛴 항목이 없는지 확인한다.

```powershell
cd openclaw\mcp-servers\factory-api
.venv\Scripts\python -m pytest -rs
```

## 다음 단계

4단계의 남은 확인과 후속 범위는 다음과 같다. 상세는 [documents/openclaw-coordinator-delegation.md](documents/openclaw-coordinator-delegation.md)의 8절과 9절에 있다.

- 불일치 응답 거부, 금지 대상 전송의 정책 거부, 대기 중 위임 1건 규칙 등 미검증 항목 확인.
- 공유 위임 세션의 정리 주기와 방법 결정.
- 요청 식별과 대조를 코드로 강제할지 결정(지금은 업무지침 수준).

3단계의 남은 확인과 후속 범위는 다음과 같다. 상세는 [documents/openclaw-factory-tools.md](documents/openclaw-factory-tools.md)의 4.6절과 7절에 있다.

- 운영 데이터가 준비되면 실제 기록의 상세 응답을 비서 대화로 검증한다.
- API 인증, 집계 API.
- main 비서의 권한(전역 `full` 프로필, Code Mode) 점검.
- 보조 모델 호출과 주기 작업의 비용, 데이터 전달 범위 점검.
- OpenClaw 인증 프로필 참조 경로 실패의 원인 확인과 `REF_SHADOWED` 경고 정리.
