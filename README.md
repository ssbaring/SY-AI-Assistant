# SY Holdings AI Assistant

공장의 입출고·계근·생산·재고·수율·회계 업무를 통합 관리하는 AI 기반 업무 시스템. 업무 데이터와 계산은 프로그램이 처리하고, AI는 문서 판독·분류·검색·설명·명령 인터페이스를 담당한다. 상세 규칙은 [CLAUDE.md](CLAUDE.md) 참고.

## 현재 진행 단계

- **0단계 (부트스트랩) 완료** — 백엔드 프로젝트 뼈대(FastAPI, DB 연결), 로컬 개발 DB(Docker PostgreSQL), 헬스체크 테스트.
- **1단계 (계근기록 스키마·마이그레이션) 완료** — `weighing_records`/`weighing_record_history` 테이블, 계근번호 자동 채번, 삭제 금지·취소 후 불변 트리거. 자세한 내용은 [documents/weighing-record-schema.md](documents/weighing-record-schema.md) 참고.
- **2단계 (계근기록 조회 API) 완료** — `GET /api/v1/weighing-records` 목록/상세 조회 3종. 자세한 내용은 [documents/weighing-query-api.md](documents/weighing-query-api.md) 참고.
- 백엔드 pytest **73개 통과**.
- **3단계 (OpenClaw 도구 연결) 진행 중** — 자세한 내용은 [documents/openclaw-factory-tools.md](documents/openclaw-factory-tools.md) 참고.
  - 완료: 조회 API 3종을 호출하는 stdio MCP 서버 `factory-api`(`openclaw/mcp-servers/factory-api/`) 구현과 자동 테스트(단위, 실제 stdio MCP 프로토콜, 실제 backend 연동).
  - **미완료**: 실제 OpenClaw 연결. 온보딩·모델 인증, MCP 서버 등록, factory-operations 비서 등록과 권한 정책 적용, 실제 비서 대화 검증은 아직 하지 않았다.

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
| OpenClaw 2026.9.8 (Windows 네이티브, Node.js v24.19.0) | CLI만 설치됨. 온보딩·Gateway·비서 등록은 하지 않음 |

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

3단계의 남은 작업인 실제 OpenClaw 연결을 진행한다: 온보딩과 모델 인증, `factory-api` MCP 서버 등록, factory-operations 비서 등록과 권한 정책 적용, 실제 비서 대화 검증. 절차는 [documents/openclaw-factory-tools.md](documents/openclaw-factory-tools.md)에 있다. coordinator 연결과 API 인증은 그 뒤 단계다.
