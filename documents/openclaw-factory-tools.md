# 3단계: OpenClaw 계근 조회 도구 (factory-api MCP 서버)

이 문서는 2단계 조회 API를 OpenClaw 비서가 호출할 수 있게 하는 stdio MCP 서버
`factory-api`의 구성, 설치, 실행, 등록, 종료 방법을 설명한다. 코드는
`openclaw/mcp-servers/factory-api/`에 있다.

## 0. 현재 상태

현재 단계는 **로컬 읽기 전용 연결 MVP 검증 완료**다. 한 대의 개발 PC에서, 비어 있는
개발 DB를 대상으로, 조회 전용 연결이 의도대로 동작하는 것을 확인했다. 회사 운영
배포나 전체 보안 검증이 끝난 것은 아니다.

| 항목 | 상태 |
|---|---|
| MCP 서버 구현(조회 도구 3개) | 완료 |
| MCP 서버 자동 테스트(단위, 실제 stdio MCP 프로토콜, 상한·정리) | 182개 통과 |
| backend 테스트 | 73개 통과 |
| 실제 테스트 DB 연동 검증(`test_e2e_backend.py`, 위 182개에 포함) | 통과 |
| OpenClaw 온보딩과 모델 인증 | 완료(6절) |
| MCP 서버 `factory-api` 등록 | 완료(4.2절) |
| `factory-operations` 비서 등록과 권한 정책 적용 | 완료(4.3, 4.4절) |
| 실제 비서 대화 검증 | 빈 목록과 상세 404 범위에서 완료(4.6절) |
| 존재하는 기록에 대한 실제 비서 응답 검증 | **하지 않음**(운영 데이터 준비 후) |
| 모델에 전달된 전체 도구 목록의 직접 확인 | **하지 못함**(4.5절의 검증 한계) |
| coordinator 연결과 위임 | 다음 단계로 보류 |
| API 인증, 집계 API | 후속 작업으로 보류(7절) |

이 문서에서 **[미검증]** 으로 표시한 내용은 설치된 OpenClaw 2026.9.8의 도움말과
패키지에 포함된 문서(`%APPDATA%\npm\node_modules\openclaw\docs`)에서 확인했지만,
실제로 실행해 동작을 확인하지는 않은 것이다. 표시가 없는 등록·정책 절차는 2026-10-07에
실제로 적용하고 확인한 것이다.

## 1. 구성

```
OpenClaw Gateway (Windows 네이티브)
   │  stdio (JSON-RPC, 자식 프로세스)
   ▼
factory-api MCP 서버 (Python, 전용 가상환경)
   │  HTTP GET (127.0.0.1 전용)
   ▼
FastAPI backend  ──►  PostgreSQL
```

- MCP 서버는 DB에 접속하지 않는다. 기존 조회 API만 호출한다.
- GET 요청만 보낸다. 경로는 코드에 고정되어 있고, 공개 메서드는 조회 3개뿐이다.
- 리다이렉트를 따라가지 않는다. 3xx 응답은 오류로 처리한다.
- 프록시 등 환경변수 설정을 쓰지 않는다(`trust_env=False`).
- stdout에는 MCP 메시지만 쓰고, 로그와 오류 안내는 stderr로 보낸다.

| 파일 | 역할 |
|---|---|
| `factory_api_mcp/__main__.py` | 실행 진입점(`python -m factory_api_mcp`) |
| `factory_api_mcp/config.py` | `FACTORY_API_BASE_URL` 읽기와 검증 |
| `factory_api_mcp/schemas.py` | 도구 입력 스키마, API 응답 스키마 |
| `factory_api_mcp/api_client.py` | GET 호출, 타임아웃, 오류 변환 |
| `factory_api_mcp/server.py` | MCP 서버와 도구 3개 정의 |
| `tests/` | 단위, 프로토콜, backend 연동 테스트 |

## 2. 도구

MCP 서버 이름은 `factory-api`다. 세 도구 모두 읽기 전용으로 표시되어 있고
(`readOnlyHint: true`), 정의되지 않은 인자는 거부한다(`additionalProperties: false`).
주소, 호스트, 경로를 받는 인자는 없다.

| 도구 | 호출하는 API | 입력 |
|---|---|---|
| `list_weighing_records` | `GET /api/v1/weighing-records` | 아래 표(모두 선택) |
| `get_weighing_record_by_ticket` | `GET /api/v1/weighing-records/ticket/{ticket_no}` | `ticket_no`: 문자열 1~20자 |
| `get_weighing_record_by_id` | `GET /api/v1/weighing-records/{record_id}` | `record_id`: 1 이상 정수 |

`list_weighing_records`의 인자는 API 쿼리 파라미터와 이름·의미가 같다.

| 인자 | 타입 |
|---|---|
| `direction` | `INBOUND` / `OUTBOUND` |
| `status` | `IN_PROGRESS` / `COMPLETED` / `CANCELED` |
| `vehicle_number`, `partner_name`, `material_name`, `ticket_no` | 문자열 |
| `started_from`, `started_to` | 문자열(타임존이 있는 ISO 8601) |
| `page`, `page_size` | 정수 |
| `sort_dir` | `asc` / `desc` |

### 입력 검증

- MCP 서버는 **타입과 열거값만** 확인한다. 문자열 자리에 숫자, 정수 자리에 문자열·
  소수·불리언이 오면 API를 호출하지 않고 거부한다.
- 필터, 날짜 범위, 타임존 필수, `page`/`page_size` 범위, 빈 문자열 규칙은
  [weighing-query-api.md](weighing-query-api.md)의 정책 그대로 **API가 판단**한다.
  MCP 서버는 같은 규칙을 다시 구현하지 않고 API의 422를 전달한다.
- 지정하지 않았거나 `null`인 인자는 요청에 넣지 않는다. API 기본값(`page=1`,
  `page_size=20`, `sort_dir=desc`)이 적용된다.
- `ticket_no`는 URL 경로의 한 칸으로 퍼센트 인코딩한다. `/`, `\`, 제어문자가 있거나
  값이 `.`, `..`, 공백뿐이면 API를 호출하지 않고 거부한다. 길이 20자는
  `weighing_records.ticket_no` 컬럼 길이와 같다.

### 출력

- 정상 결과는 API 응답과 같은 구조다. `structuredContent`와, 같은 내용을 JSON
  문자열로 담은 `content`를 함께 준다.
  - 목록: `items`, `page`, `page_size`, `total`, `total_pages`
  - 목록 항목: `id`, `ticket_no`, `direction`, `status`, `vehicle_no`, `partner_name`,
    `item_name`, `operator_name`, `gross_weight_kg`, `tare_weight_kg`, `net_weight_kg`,
    `first_weighed_at`, `created_at`, `updated_at`
  - 상세: 목록 항목 + `cancelled_at`, `cancelled_by`, `cancel_reason`
- **필드 허용 목록**: 위에 없는 필드는 API가 보내더라도 결과에서 뺀다.
- **구조 검증**: 필수 필드가 없거나, 타입이 다르거나(예: 정수 자리에 문자열),
  열거값이 아니거나, 시각이 타임존 있는 ISO 8601이 아니거나, JSON이 아니면
  오류로 처리한다. 응답이 5MB를 넘어도 오류다(아래 "응답 크기 상한").
- 시각은 API가 준 UTC(`+00:00`) 문자열 그대로이고, 중량은 kg 정수 그대로다.
  MCP 서버는 합계, 단위 변환 등 어떤 계산도 하지 않는다.

### 오류

오류는 모두 `isError: true` 결과로 반환하며, 아래 고정 문구만 담는다. 예외 내용,
스택트레이스, API 주소, SQL, API 오류 응답 본문은 도구 결과에 넣지 않는다.
원인은 stderr 로그에 예외 종류와 상태코드만 남긴다.

| 상황 | 도구 결과 문구 |
|---|---|
| 404 | `해당 계근기록을 찾을 수 없습니다.` |
| 422 | `입력값이 올바르지 않습니다. <필드>: <메시지>; ...` |
| 3xx, 그 밖의 4xx, 5xx | `API 요청을 처리하지 못했습니다. (HTTP <상태코드>)` |
| 연결 실패, 연결 타임아웃 | `API 서버에 연결할 수 없습니다.` |
| 읽기·쓰기·풀 타임아웃, 전체 15초 상한 초과 | `API 응답 시간이 초과되었습니다.` |
| 그 밖의 전송 오류 | `API 요청을 처리하지 못했습니다.` |
| JSON이 아님, 구조 불일치, 크기 초과 | `API 응답 형식이 올바르지 않습니다.` |
| 도구 인자 오류 | `입력값이 올바르지 않습니다. <필드>: <메시지>; ...` |
| 없는 도구 이름 | `알 수 없는 도구입니다.` |
| 예상하지 못한 내부 오류 | `도구 실행 중 오류가 발생했습니다.` |

422는 API 검증 오류에서 필드 이름과 메시지만 전달한다. 입력값(`input`), `ctx`,
`url`은 버리고, 최대 10건, 건당 200자로 자른다.

### 타임아웃과 재시도

| 종류 | 값 | 정확한 의미 |
|---|---|---|
| connect | 3초 | API 서버와 TCP 연결을 맺을 때까지 기다리는 시간 |
| read | 10초 | 응답 데이터의 **다음 조각**이 도착하기를 기다리는 시간. 응답 전체에 걸리는 총 시간의 한도가 아니다 |
| write | 10초 | 요청 데이터의 한 조각을 보내는 데 기다리는 시간. GET은 본문이 없어 사실상 헤더 전송 시간이다 |
| pool | 3초 | 연결 풀에서 연결을 얻을 때까지 기다리는 시간. 호출마다 클라이언트를 새로 만들어 닫으므로 실제로 대기할 일은 거의 없다 |
| **전체** | **15초** | 연결을 시작해서 응답 본문을 다 읽을 때까지의 총 시간. 위 네 가지와 별개로 MCP 서버가 직접 강제한다 |

- 자동 재시도는 하지 않는다. 실패는 한 번의 요청으로 끝난다.
- 전체 상한이 필요한 이유: read 타임아웃은 조각 사이의 간격만 본다. 응답이 조금씩
  계속 도착하면 read 타임아웃에 걸리지 않은 채 끝없이 이어질 수 있다. 전체 15초가
  지나면 응답을 읽던 중이라도 중단하고 `API 응답 시간이 초과되었습니다.`를 반환한다.
- 시간 초과, 오류, 호출 취소 어느 경우에도 응답 스트림과 HTTP 연결을 닫는다. 호출
  취소(MCP 클라이언트의 요청 취소 등)는 오류 결과로 바꾸지 않고 그대로 전파한다.
- 4절의 OpenClaw 서버별 요청 타임아웃(`--timeout 20`)은 MCP 서버 자체가 응답하지
  않을 때를 대비한 바깥쪽 보조 장치다. 위 15초 상한은 그 설정과 무관하게 동작한다.

### 응답 크기 상한

- 응답 본문은 5MB까지만 받는다. 본문을 한꺼번에 읽지 않고 조각 단위로 읽으면서
  **실제로 받은 바이트 수**를 세고, 5MB를 넘는 순간 읽기를 중단하고 연결을 닫는다.
- `Content-Length` 헤더는 판단에 쓰지 않는다. 헤더가 없거나 실제보다 작게 적혀
  있어도 같은 방식으로 적용된다.
- 압축 응답을 요청하지 않는다(`Accept-Encoding: identity`). 서버가 그래도 압축해
  보내면 풀린 뒤의 크기를 세므로 메모리 사용량은 상한을 넘지 않는다.
- 본문은 200과 422일 때만 읽는다. 그 밖의 상태코드는 본문을 읽지 않고 버린다.
- 상한을 넘으면 `API 응답 형식이 올바르지 않습니다.`를 반환한다.

## 3. 설치와 실행

### 가상환경

backend와 분리된 전용 가상환경을 쓴다. `mcp` 패키지가 `uvicorn`, `starlette` 등을
필수로 끌어와 backend의 고정 버전(`uvicorn==0.53.0`)과 어긋나기 때문이다.

```powershell
cd openclaw\mcp-servers\factory-api
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pip check
```

| 직접 의존성 | 버전 | 비고 |
|---|---|---|
| `mcp` | 2.3.0 | MCP SDK. 2.x는 `httpx2>=2.10.0`에 의존한다 |
| `httpx2` | 2.13.1 | HTTP 클라이언트. backend 개발 의존성(`httpx2>=2.13.1,<3`)과 같은 버전 |
| `pytest` | `>=9.1,<10` | 개발용. backend와 같은 범위 |

전이 의존성 잠금 파일은 만들지 않았다. 설치 시점에 따라 전이 의존성 버전이
달라질 수 있다(2026-10-05 설치 기준 `uvicorn 0.54.0`, `starlette 1.7.0`,
`pydantic 2.13.5`, `anyio 4.15.1` 등).

### 환경변수

`FACTORY_API_BASE_URL`은 **필수**다. 없거나 형식이 다르면 서버는 stderr에 이유를
출력하고 종료코드 2로 끝난다(stdout에는 아무것도 쓰지 않는다).

- 허용 형식은 `http://127.0.0.1:<포트>` 하나뿐이다. 개발 기본값은
  `http://127.0.0.1:8000`이고, 테스트는 임시 포트를 쓴다.
- 거부하는 값: 다른 호스트(`localhost`, `host.docker.internal`, 다른 IP 포함),
  `https`, 계정정보(`user:pass@`), 경로(끝의 `/` 포함), 쿼리, fragment, 포트 생략.

### 직접 실행(확인용)

평소에는 OpenClaw Gateway가 자식 프로세스로 실행한다. 직접 실행하면 stdin에서
JSON-RPC를 기다리는 상태가 되며, stdin을 닫거나(Ctrl+Z 후 Enter) Ctrl+C로 종료한다.

```powershell
cd openclaw\mcp-servers\factory-api
$env:FACTORY_API_BASE_URL = "http://127.0.0.1:8000"
.venv\Scripts\python -m factory_api_mcp
```

### 테스트

```powershell
cd openclaw\mcp-servers\factory-api
.venv\Scripts\python -m pytest -rs
```

| 파일 | 내용 | 필요한 것 |
|---|---|---|
| `test_config.py` | 기본 주소 검증 | 없음 |
| `test_api_client.py` | GET 전용, 리다이렉트 차단, 응답 구조 검증, 상태코드·타임아웃 처리, 클라이언트 정리 | 없음(`MockTransport`) |
| `test_tools.py` | 도구 정의, 입력 검증, 오류 비노출 | 없음 |
| `test_mcp_protocol.py` | 서버를 자식 프로세스로 띄워 `initialize`, `tools/list`, `tools/call`(정상·오류), 시작 거부, stdout 순수성 확인 | 없음(임시 포트의 가짜 API) |
| `test_limits.py` | 전체 15초 상한, 5MB 크기 상한(읽는 도중 중단, `Content-Length` 없는 응답), 시간 초과·취소 시 응답과 연결 정리. 실제 소켓과 실제 MCP 자식 프로세스로도 확인하며, 그중 한 테스트는 실제로 15초를 기다린다 | 없음(임시 포트의 가짜 API) |
| `test_e2e_backend.py` | 실제 FastAPI + 테스트 DB에 연결해 도구 결과를 API 직접 호출 결과와 비교 | backend 가상환경, PostgreSQL 컨테이너 |

- 연동 테스트는 backend 테스트와 같은 규칙으로 DB를 정한다. 이름이 `_test`로
  끝나는 DB만 쓰고, 아니면 실행을 거부한다. 시드 데이터는 테스트 DB에만 넣고
  끝나면 비운다. 개발 DB에는 접속하지 않는다.
- backend 서버는 임시 포트에 띄우고 테스트가 끝나면(실패 포함) 종료한다.
- backend 가상환경이나 PostgreSQL이 없으면 연동 테스트는 **건너뛴다**. `-rs`
  출력에 SKIPPED가 있으면 연동 검증을 한 것이 아니다.

## 4. OpenClaw 등록과 실제 검증

아래 절차는 2026-10-07에 개발 PC에서 실제로 적용했다. 모두 사용자 OpenClaw 설정
(`~\.openclaw\openclaw.json`)을 바꾸는 명령이며 저장소 파일은 바꾸지 않는다. 설정을
바꾸기 전에는 매번 새 이름으로 백업했다. 경로의 `<저장소>`는 이 저장소의 절대
경로로 바꾼다.

### 4.1 온보딩

`openclaw onboard`로 모델 제공자와 인증을 설정했다. 선택한 값과 겪은 문제는 6절에 있다.

### 4.2 MCP 서버 등록

근거: `openclaw mcp add --help`, `docs/tools/mcp.md`, `docs/cli/mcp/registry.md`.

```powershell
openclaw mcp add factory-api `
  --command "<저장소>\openclaw\mcp-servers\factory-api\.venv\Scripts\python.exe" `
  --arg -m --arg factory_api_mcp `
  --cwd "<저장소>\openclaw\mcp-servers\factory-api" `
  --env FACTORY_API_BASE_URL=http://127.0.0.1:8000 `
  --include "list_weighing_records,get_weighing_record_by_ticket,get_weighing_record_by_id" `
  --timeout 20 --connect-timeout 5

openclaw mcp doctor factory-api --probe
```

- 설정의 `mcp.servers.factory-api`에 저장된다(명령, 인자, 작업 폴더, 환경변수,
  도구 필터, 연결 5초 / 요청 20초).
- `openclaw mcp doctor factory-api --probe`는 `ok`였고, `openclaw mcp probe factory-api --json`은
  도구를 정확히 3개 반환했다: `factory-api__list_weighing_records`,
  `factory-api__get_weighing_record_by_ticket`, `factory-api__get_weighing_record_by_id`.
- probe는 MCP 연결(`initialize`, `tools/list`)만 확인한다. 실제 API 호출 확인은
  4.6절의 비서 대화 검증이다.
- backend API(`127.0.0.1:8000`)가 떠 있지 않아도 등록과 probe는 됐다.
- `--include`는 서버가 내놓는 도구 중 노출할 것을 고르는 필터다. 나중에 서버에
  도구가 늘어도 자동으로 노출되지 않게 하는 두 번째 방어선이다. 서버에 도구를
  추가해 필터가 실제로 막는지는 시험하지 않았다. **[미검증]**

### 4.3 비서 workspace 복사와 등록

실제 비서 workspace는 **저장소 밖 복사본**을 쓴다. OpenClaw가 workspace 안에
파일을 만들거나 고칠 수 있어, 저장소 폴더를 직접 가리키면 작업 트리가 오염될 수
있기 때문이다. 저장소의 `openclaw/workspaces/factory-operations/`는 원본으로
관리하고, 지침을 바꾸면 복사본에 다시 반영한다.

```powershell
Copy-Item -Recurse `
  "<저장소>\openclaw\workspaces\factory-operations" `
  "$env:USERPROFILE\.openclaw\workspace-factory-operations"

openclaw agents add factory-operations `
  --workspace "$env:USERPROFILE\.openclaw\workspace-factory-operations" `
  --non-interactive
```

- 근거: `openclaw agents add --help`, `docs/cli/agents.md`,
  `docs/concepts/multi-agent.md`(다른 비서의 기본 workspace 위치가
  `<상태 폴더>/workspace-<agentId>`).
- 비서 ID `factory-operations`(하이픈 포함)로 등록됐다.
- 등록 과정에서 OpenClaw가 workspace 복사본에 `IDENTITY.md`를 새로 만들었다. 저장소
  밖 복사본을 쓴 덕분에 저장소에는 영향이 없었다.
- 비서가 2개가 되면서 `agents add`가 다음 값을 자동으로 추가했다. 기존 동작을
  `main`에 고정하는 값이다: `agents.ownership: "explicit"`,
  `agents.defaults.heartbeat.agentId: "main"`,
  `agents.defaults.systemAgent.agentId: "main"`, `talk.agentId: "main"`.

### 4.4 권한 정책

문서에서 확인한 규칙(`docs/gateway/config-tools/tool-policy.md`,
`docs/tools/multi-agent-sandbox-tools.md`):

- 적용 순서는 프로필 → 전역 allow/deny → 비서별 allow/deny → 샌드박스다. 각 단계는
  좁히기만 하고 앞 단계에서 빠진 도구를 되살리지 못한다.
- 비서별 `tools.profile`은 그 비서에 한해 전역 `tools.profile`을 대체한다.
- `allow`가 비어 있지 않으면 나머지는 모두 차단되고, `deny`가 항상 우선한다.
- `coding`과 `messaging` 프로필은 설정된 MCP 서버 도구(`bundle-mcp`)를 암묵
  허용한다. `minimal`은 MCP 도구를 숨긴다.
- 로컬 온보딩은 프로필이 없으면 전역 `tools.profile`을 `"full"`로 설정한다.
- MCP 도구의 정책상 이름은 `<서버>__<도구>` 형식이다. 서버 이름의 영문·숫자·`_`·`-`
  외 문자는 `-`로 바뀐다. `factory-api`는 그대로 쓰인다.

적용한 정책(`openclaw.json`의 해당 부분):

```json5
{
  agents: {
    entries: {
      "factory-operations": {
        workspace: "~/.openclaw/workspace-factory-operations",
        tools: {
          profile: "messaging",
          allow: [
            "factory-api__list_weighing_records",
            "factory-api__get_weighing_record_by_ticket",
            "factory-api__get_weighing_record_by_id",
          ],
          codeMode: false, // 4.5절
        },
      },
      main: {
        tools: { deny: ["factory-api__*"] },
      },
    },
  },
}
```

적용 방법: 비서별 `tools`와 `main`의 `deny`는 `openclaw config set --batch-file <파일>`로,
`codeMode`는 `openclaw config set agents.entries.factory-operations.tools.codeMode false --strict-json`
으로 넣었다. 둘 다 먼저 `--dry-run`으로 검증했고, 적용 뒤 `openclaw config validate`가
통과했다.

- `factory-operations`: 프로필이 MCP 도구를 통과시키고, `allow`가 정확히 3개로
  좁힌다. 실제 실행에서 도구 목록은 MCP 도구 3개, OpenClaw 내장 도구 0개였다(4.6절).
- `main`: 실제 실행에서 도구 목록에 MCP 도구가 0개였다(4.6절).
- 비서를 새로 추가하면 그 비서에는 `factory-api__*` 차단이 자동으로 걸리지 않는다.
  전역 `tools.deny`에 넣으면 `factory-operations`까지 막히므로(deny가 항상 우선)
  비서별로 넣어야 한다.
- `minimal` 프로필과 `alsoAllow` 조합은 쓰지 않는다. `minimal`이 MCP 도구를 숨기고,
  `alsoAllow`는 `session_status`, `gateway`를 남겨 "3개만"이 되지 않는다. 같은 범위에
  `allow`와 `alsoAllow`를 함께 쓰면 설정 검증에서 거부된다.
- `mcp.servers`는 전역 설정이라 다른 비서에도 보인다. 다른 비서에는 `deny`로 막는다.
  coordinator의 위임 연결은 다음 단계에서 정한다.

상위 정책에서 확인할 것(현재 설정은 모두 해당 없음. 나중에 바꿀 때 확인한다) **[미검증]**:

- 전역 `tools.deny`에 `bundle-mcp`나 `factory-api__*`가 없어야 한다.
- 전역 `tools.allow`를 쓴다면 위 3개가 들어 있어야 한다.
- 샌드박스 모드를 켜면 샌드박스 도구 허용 목록에도 3개(또는 `factory-api__*`)를
  넣어야 한다.
- 제공자별 정책(`tools.byProvider`)을 쓴다면 같은 조건을 확인한다.

### 4.5 도구 노출 방식: Code Mode 끔, Tool Search 유지

OpenClaw는 도구 목록을 모델에 그대로 보여주지 않고 간접 호출 방식을 자동으로 쓸 수
있다. 이 비서의 최종 구성은 다음과 같다.

| 방식 | 이 비서의 상태 | 모델이 호출하는 것 |
|---|---|---|
| Code Mode | **끔**(`agents.entries.factory-operations.tools.codeMode: false`) | `exec` / `wait`(JavaScript 실행) |
| Tool Search | **유지**(전역 `tools.toolSearch` 미설정 = 자동) | `tool_search` / `tool_describe` / `tool_call` |

- 조회 도구 3개는 모델에 **직접 노출되지 않는다.** 모델은 Tool Search의 제어 도구를
  거쳐 3개 도구를 찾고 호출한다. 호출할 수 있는 대상은 정책이 허용한 3개뿐이다.
- **Code Mode를 끈 이유**: 첫 검증에서 이 비서가 Code Mode의 `exec`로 도구를 부르는
  것이 확인됐다. Code Mode의 기본 실행기는 Node의 `node:vm`이고, OpenClaw 문서
  (`docs/tools/code-mode/executors.md`)는 이것이 보안 경계가 아니라고 밝힌다. 조회
  전용 비서에 코드 실행 경로를 둘 이유가 없어 껐다.
- **다시 켜지지 않는 근거**: 문서의 활성화 우선순위는 ① 비서별 모델 설정
  (`agents.entries.<비서>.models[...].codeMode`) → ② 비서별 `tools.codeMode` →
  ③ 기본 모델 설정 → ④ 전역 `tools.codeMode`다. ②를 `false`로 두었고 ①은 설정하지
  않았으므로 전역 자동 설정이 이 비서의 Code Mode를 켜지 못한다. 나중에 ①에
  `codeMode: true`를 넣으면 다시 켜진다.
- **Tool Search를 유지한 이유**: `tool_call`은 코드 실행이 아니라 "도구 ID와 인자"를
  넘기는 구조화된 호출이다. 끄려면 전역 `tools.toolSearch: false`가 필요한데, 스키마상
  비서별 설정이 없어 이번 단계에서는 바꾸지 않았다.

**검증 한계**: 모델에 전달된 전체 도구 목록(요청 본문의 도구 이름)은 이 비서의
실행에서는 로그에 남지 않아 직접 확인하지 못했다. 확인한 것은 ⓐ 로그의
`tool-search: cataloged 3 tools`와 `code-mode … "active":false`, ⓑ 세션 기록에
남은 실제 호출 이름(`tool_search`, `tool_describe`, `tool_call`뿐)이다. "제어 도구
외에 다른 도구가 모델에 보이지 않았다"는 것은 이 근거에 따른 판단이지 직접 관찰이 아니다.

### 4.6 실제 비서 검증 결과

2026-10-07, 개발 PC에서 Gateway와 FastAPI를 수동 실행하고 `openclaw agent --agent <비서>
--session-key <새 키> --message "…"`로 한 번씩 확인했다. 개발 DB는 비어 있었고 읽기만
했다. 테스트 데이터는 만들지 않았다.

| 요청 | 호출된 도구와 결과 | 비서의 답 |
|---|---|---|
| 최근 계근기록 5건 | `list_weighing_records` → API 200, 0건 | 0건으로 안내 |
| 없는 계근번호 상세 | `get_weighing_record_by_ticket` → API 404 | 기록 없음으로 안내, 내용을 만들지 않음 |
| 없는 ID 상세 | `get_weighing_record_by_id` 2회(아래) → API 404 | 기록 없음으로 안내 |
| 오늘 입고 중량 합계 | `list_weighing_records`(입고, 오늘 범위) → API 200, 0건 | 집계 기능이 없다고 안내. 직접 계산하지 않았고 0 kg으로 단정하지 않음 |
| `main`에게 계근 조회 요청 | 업무 도구 호출 0회 | "factory-api 도구가 없습니다." |

- **`factory-operations`**: 네 번의 실행 모두 Code Mode가 꺼져 있었고, `exec`와 `wait`는
  호출되지 않았다. 도구 목록은 MCP 도구 3개였다. 조회 도구 3개가 모두 한 번 이상 호출됐다.
- **`main`**: 도구 목록에 MCP 도구가 0개였고(내장 도구 55개), MCP 도구 호출은 0회였다.
  FastAPI에도 추가 요청이 없었다. `main`은 Code Mode가 켜진 채로 동작한다(7절).
- **ID 입력 오류와 복구**: ID 상세 조회에서 모델이 첫 호출에 인자 이름을 `id`로
  보냈다. MCP 서버의 입력 검증이 `record_id: Field required; id: Extra inputs are not
  permitted`로 거부했고 이 호출은 API에 도달하지 않았다. 모델이 `record_id`로 고쳐 다시
  호출했고 API가 404를 반환했다. 비서는 이 과정을 답변에 사실대로 적었다. 정의되지
  않은 인자를 거부하는 검증과 오류 후 복구가 실제로 동작한 사례다.
- **0건과 404의 해석**: 비어 있는 개발 DB에서 예상한 결과다. 비서가 "데이터 소스나
  환경을 확인해 달라"고 덧붙였지만 장애는 아니다.

**성공 경로의 검증 범위**

| 경로 | 검증 수준 |
|---|---|
| 존재하는 기록의 목록·상세 성공 응답 | 자동 연동 테스트(`test_e2e_backend.py`, 테스트 DB)에서 MCP 서버 수준까지 검증 |
| 실제 비서 대화 | 빈 목록(0건)과 상세 404만 검증 |
| 실제 데이터의 상세 응답을 비서가 정확히 전달하는지 | **미검증.** 운영 데이터가 준비된 뒤 확인한다 |

**이전 구성에서의 첫 검증(참고)**: Code Mode를 끄기 전에도 같은 요청을 확인했다. 그때는
이 비서가 Code Mode의 `exec`를 거쳐 목록 조회와 계근번호 상세 조회를 불렀고(ID 상세는
호출되지 않음) 결과는 위와 같았다. 이 확인에서 Code Mode 경로가 드러나 4.5절의 결정을 했다.

## 5. 종료와 등록 해제

- MCP 서버는 Gateway가 자식 프로세스로 실행하고, Gateway를 멈추면 함께 정리된다
  (`docs/cli/mcp/registry.md`: 소유한 stdio 자식 프로세스는 정리 시 종료). `mcp probe`
  뒤에 서버 프로세스가 남지 않는 것은 확인했다. Gateway 종료 뒤의 정리는 따로
  확인하지 않았다. **[미검증]**
- 서버는 stdin이 닫히면 스스로 종료한다(프로토콜 테스트에서 종료코드 0 확인).
- 포그라운드 Gateway(`openclaw gateway run`)는 Ctrl+C로 멈춘다(실제로 이렇게 운용했다).
  예약 작업은 설치하지 않았다. 설치했다면 `Stop-ScheduledTask` 또는
  `schtasks /end /tn "OpenClaw Gateway"`로 멈춘다. **[미검증]**
- 일시 중지와 재개: `openclaw mcp configure factory-api --disable` / `--enable`
  (`openclaw mcp configure --help`에서 확인). 정의는 남기고 연결만 하지 않는다. **[미검증]**
- 등록 해제: `openclaw mcp unset factory-api`. **[미검증]**
- backend API는 별도 프로세스다. `uvicorn`을 실행한 터미널에서 Ctrl+C로 멈춘다.

## 6. 온보딩, 인증, 실행 참고

비밀값 원칙: API 키와 Gateway 토큰의 값은 저장소, 문서, 설정 파일 평문, 명령줄,
대화 기록 어디에도 남기지 않는다. 확인이 필요할 때는 값이 아니라 참·거짓만 출력한다.

### 6.1 선택한 구성

| 항목 | 선택 |
|---|---|
| 모델 제공자와 인증 | Anthropic API 키(사용량 과금). 계근 데이터가 모델 제공자로 전송된다 |
| 기본 모델 | `anthropic/claude-sonnet-5-5` |
| 키 보관 | 환경변수 참조(SecretRef). 설정에는 변수 이름만 있고 값은 없다 |
| Gateway | `loopback`, 포트 18789, 토큰 인증(토큰은 OpenClaw secret store 참조), 필요할 때 수동 실행 |
| 제외한 것 | 채널, 웹 검색, 추가 스킬, 훅, 예약 작업(daemon) 설치 |
| 전역 `tools.profile` | 온보딩 기본값 `"full"` 그대로(7절) |

온보딩 명령은 클래식 마법사의 수동 흐름을 썼다(`openclaw onboard --flow manual --mode local
--secret-input-mode ref --gateway-bind loopback --gateway-auth token --tailscale off
--no-install-daemon --skip-channels --skip-search --skip-skills --skip-hooks --skip-ui
--skip-health`).

### 6.2 인증에서 겪은 문제와 현재 구성

- **온보딩 중 AI 테스트 실패**: 환경변수 참조 방식에서는 클래식 마법사의 "Test AI
  access now?"가 `secret reference was not materialized by the active runtime`로
  실패했다. 설치된 코드를 읽은 바로는 이 테스트 경로에 참조를 실제 값으로 푸는 단계가
  보이지 않는다. 코드를 읽어 내린 판단이며 실행으로 확인한 원인은 아니다. 테스트에
  "No"를 골라 온보딩을 끝냈다(코드상 "Continue anyway"는 모델·인증 설정을 저장하지 않는다).
- **인증 프로필 참조 경로 실패**: 온보딩이 만든 인증 프로필(`anthropic:default`의
  `keyRef`)은 Gateway의 실제 요청에서도 같은 오류로 실패했다. Gateway 재시작과
  `openclaw secrets reload`로도 해결되지 않았다. **정확한 원인은 확정하지 못했다.**
  비슷한 증상의 이슈가 OpenClaw 저장소에 열려 있다(openclaw/openclaw #146969).
- **현재 구성(동작 확인)**: 키 참조를 설정 수준으로 옮겼다. 이 구성으로 Gateway를 통한
  실제 모델 호출이 성공했다.

  ```json5
  models: { providers: { anthropic: {
    auth: "api-key",
    apiKey: { source: "env", provider: "default", id: "<키 환경변수 이름>" },
  } } }
  ```

- **남아 있는 경고**: `openclaw secrets audit --check`가 `REF_SHADOWED` 1건을 보고한다
  (plaintext 0, unresolved 0). 같은 제공자의 인증 프로필이 저장소에 남아 있으면 나오는
  일반 경고이고, Gateway 에이전트 요청은 설정 수준 참조를 쓴다. 기존 프로필과 온보딩 중
  생긴 대기 후보 프로필은 삭제하지 않고 그대로 두었다. 정리는 후속 결정 사항이다.

### 6.3 키 환경변수 운용

- 키는 Gateway를 실행하는 PowerShell 창에만 임시로 넣는다. 영구 등록하지 않았으므로
  **새 터미널을 열 때마다 다시 입력해야 한다.**
- 변수 이름은 `ANTHROPIC_API_KEY`가 아닌 전용 이름을 쓴다. 표준 이름을 쓰면 같은
  창에서 실행한 Claude Code가 구독 대신 그 키로 과금된다.
- 입력은 가려진 프롬프트로 한다. 키가 명령줄과 명령 기록에 남지 않는다.

  ```powershell
  $s = Read-Host "Anthropic API key" -AsSecureString
  $env:<키 환경변수 이름> = [System.Net.NetworkCredential]::new("", $s).Password
  Remove-Variable s
  ```

- 값을 출력하지 않고 존재 여부와 형식을 확인한다. 모델 호출이 네트워크로 나가기 전에
  `fetch failed`(cause `UND_ERR_INVALID_ARG`)로 실패한 적이 있다. 이 오류의 원인은
  확정하지 못했다. 다시 나오면 먼저 키 문자열에 제어문자 등이 섞였는지 확인한다.

  ```powershell
  $k = $env:<키 환경변수 이름>
  "set                  : " + [bool]$k
  "printable ASCII only : " + ($k -cmatch '^[\x21-\x7E]+$')
  "control or whitespace: " + ($k -match '[\x00-\x20\x7F]')
  "expected prefix      : " + $k.StartsWith('sk-ant-')
  ```

- `openclaw config get gateway.auth`처럼 상위 키를 조회하면 토큰이 출력될 수 있으므로
  `gateway.bind`, `gateway.auth.mode`처럼 필요한 하위 키만 조회한다.

### 6.4 실행 순서

1. Docker의 개발 DB 컨테이너가 켜져 있는지 확인한다.
2. 창 1: `backend`에서 `.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000`.
3. 창 2: 키 환경변수를 넣고 `openclaw gateway run`.
4. 창 3: `openclaw agent --agent factory-operations --message "…"`.
5. 끝나면 창 2와 창 1을 Ctrl+C로 종료한다. Gateway를 켜 둔 동안에는 7절의 주기 작업이
   모델을 호출할 수 있다.

## 7. 알려진 제한과 후속 작업

- **`main`의 권한**: `main`은 전역 `tools.profile: "full"`과 Code Mode(자동)를 그대로
  유지한다. 계근 도구는 차단돼 있지만 내장 도구는 넓게 열려 있다. 이번 단계의 권한
  제한은 `factory-operations`에만 적용했다.
- **보조 모델 호출**: 비서 대화 중 OpenClaw의 기본 보조 모델(`claude-haiku-4-5`) 호출이
  로그에서 관찰됐다. 용도와 비용, 어떤 내용이 전달되는지는 확인하지 못했다.
- **주기 작업**: Gateway는 시작할 때 `main`의 heartbeat(30분 간격) 등 주기 작업을
  등록한다. 보조 모델과 주기 작업의 비용, 데이터 전달 범위는 후속 점검 대상이다.
- **검증 범위**: 한 대의 개발 PC, 비어 있는 개발 DB, 수동 실행 기준이다. 운영 배포,
  다중 사용자, 외부 채널 연결, 전체 보안 검증은 하지 않았다.
- **API 인증 없음**: 조회 API에 인증이 없다. 이번 단계는 `127.0.0.1` 전용 로컬
  조회 연결만 다룬다. API 인증과, MCP 서버가 인증정보를 전달하는 방식은 후속 작업이다.
- **coordinator 미연결**: 총괄 비서 연결과 위임은 후속 범위다.
- **호스트 제한**: `127.0.0.1`만 허용한다. OpenClaw를 WSL2나 Docker에서 실행하거나
  API를 다른 서버에 두려면 `config.py`의 허용 규칙을 바꿔야 한다.
- **집계 없음**: 도구는 기록을 조회만 한다. 중량 합계와 집계는 프로그램이 계산한
  결과만 보고한다는 원칙에 따라, 집계 API가 생기기 전까지 비서는 합계를 답하지 않고
  기능의 한계를 안내한다(`factory-operations/AGENTS.md`).
- **전이 의존성 미고정**: 잠금 파일이 없다.
- **SDK 호환성**: `mcp 2.3.0`의 저수준 `Server` API를 쓴다. 고수준 `MCPServer`는
  정의되지 않은 인자를 무시하고 스키마에 `additionalProperties: false`를 넣지 않아
  쓰지 않았다. OpenClaw 2026.9.8의 MCP 클라이언트와의 실제 연결은 `doctor --probe`와
  실제 도구 호출로 확인했다(4.2, 4.6절).
