# SY Holdings AI Assistant

공장의 입출고·계근·생산·재고·수율·회계 업무를 통합 관리하는 AI 기반 업무 시스템. 업무 데이터와 계산은 프로그램이 처리하고, AI는 문서 판독·분류·검색·설명·명령 인터페이스를 담당한다. 상세 규칙은 [CLAUDE.md](CLAUDE.md) 참고.

## 현재 진행 단계

**0단계 (부트스트랩) 완료** — 백엔드 프로젝트 뼈대(FastAPI, DB 연결), 로컬 개발 DB(Docker PostgreSQL), 헬스체크 테스트까지 마쳤다. 아직 데이터 모델, 업무 API, OpenClaw 연결은 없음.

## 사전 준비 상태 (이 PC 기준)

| 항목 | 상태 |
|---|---|
| Python 3.12.10 가상환경 (`backend/.venv`) | 구성 완료 |
| 운영·개발 의존성 (`requirements.txt`, `requirements-dev.txt`) | 구성 완료 (운영 의존성은 버전 고정) |
| Git | 설치됨 |
| Docker Desktop / PostgreSQL 16 컨테이너 | 구성 완료 (`127.0.0.1:5432`에서만 접속) |
| pytest | 6개 통과 |
| 0단계 | 완료 |

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

`requirements.txt`는 서버 실행에 필요한 라이브러리, `requirements-dev.txt`는 여기에 테스트 도구(pytest, httpx)를 더한 개발용 목록이다.

## 로컬 개발 전용 계정 안내

`docker-compose.yml`, `backend/.env.example`에 있는 `app / app` 계정은 **내 PC의 개발용 Docker DB에서만 쓰는 임시 계정**이다. 운영·공용 서버에는 절대 사용하지 않으며, 실제 비밀번호는 코드나 Git에 넣지 않는다.

## 테스트 실행

개발 DB(`sy_holdings_dev`)와 분리된 테스트 DB(`sy_holdings_dev_test`)를 사용한다. 테스트 DB는 첫 실행 때 자동으로 만들어지고, 개발 DB의 데이터는 읽거나 바꾸지 않는다. 실행 전에 PostgreSQL 컨테이너가 켜져 있어야 한다.

```powershell
cd backend
.venv\Scripts\python -m pytest
```

## 다음 단계

1단계(계근기록 최소 데이터 모델 + 마이그레이션)로 진행한다.
