# 3단계: OpenClaw 계근 조회 도구 (factory-api MCP 서버)

이 문서는 2단계 조회 API를 OpenClaw 비서가 호출할 수 있게 하는 stdio MCP 서버
`factory-api`의 구성, 설치, 실행, 등록, 종료 방법을 설명한다. 코드는
`openclaw/mcp-servers/factory-api/`에 있다.

## 0. 현재 상태

| 항목 | 상태 |
|---|---|
| MCP 서버 구현(조회 도구 3개) | 완료 |
| 단위 테스트, 실제 stdio MCP 프로토콜 테스트, 실제 backend 연동 테스트 | 완료(182개 통과) |
| 실제 OpenClaw 권한 정책 검증(비서에게 도구 3개만 보이는지) | **하지 않음** |
| OpenClaw 온보딩과 모델 인증 | **하지 않음** |
| MCP 서버 등록(`openclaw mcp add`) | **하지 않음** |
| factory-operations 비서 등록과 권한 정책 적용 | **하지 않음** |
| 실제 비서 대화 검증 | **하지 않음** |
| coordinator 연결과 위임 | 다음 단계로 보류 |
| API 인증 | 후속 작업으로 보류(7절) |

이 문서에서 **[미검증]** 으로 표시한 내용은 설치된 OpenClaw 2026.9.8의 도움말과
패키지에 포함된 문서(`%APPDATA%\npm\node_modules\openclaw\docs`)에서 확인했지만,
실제 설정에 적용해 동작을 확인하지는 않은 것이다.

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

## 4. OpenClaw 등록 (아직 실행하지 않음)

아래 절차는 **실행하지 않았다.** 모두 사용자 OpenClaw 설정(`~\.openclaw`)을 바꾸는
명령이므로 온보딩 뒤에 승인을 받고 실행한다. 경로의 `<저장소>`는 이 저장소의
절대 경로로 바꾼다.

### 4.1 온보딩

`openclaw onboard`로 모델 제공자와 인증을 설정한다. 결정할 항목은 6절에 있다.

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

- 설정의 `mcp.servers.factory-api`에 저장된다. `add`는 저장 전에 서버에 실제로
  연결해 본다(`--no-probe`로 생략 가능). **[미검증]**
- `doctor --probe`는 서버에 연결해 도구 목록을 확인한다. 문서상 모델 인증 없이
  동작한다. **[미검증]**
- `--include`는 서버가 내놓는 도구 중 노출할 것을 고르는 필터다. 나중에 서버에
  도구가 늘어도 자동으로 노출되지 않게 하는 두 번째 방어선이다. **[미검증]**
- backend API(`127.0.0.1:8000`)가 떠 있지 않아도 등록과 probe는 된다. 도구
  호출만 `API 서버에 연결할 수 없습니다.`로 실패한다.

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
  `<상태 폴더>/workspace-<agentId>`). **[미검증]**
- 비서 ID에 하이픈(`factory-operations`)을 쓸 수 있는지는 확인하지 않았다. **[미검증]**

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

아래 정책은 문서를 근거로 한 **제안**이다. 실제 OpenClaw 설정에 적용해 보지 않았고,
비서에게 도구 3개만 보이는지, 다른 비서에게 차단되는지는 **아직 검증하지 않았다.**
4.5절의 실제 비서 검증을 마치기 전에는 권한이 의도대로 동작한다고 볼 수 없다.

제안 정책(`openclaw.json`에 들어갈 내용) **[미검증]**:

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
        },
      },
      // coordinator를 포함한 다른 모든 비서
      "<다른 비서 ID>": {
        tools: { deny: ["factory-api__*"] },
      },
    },
  },
}
```

- `factory-operations`: 프로필이 MCP 도구를 통과시키고, `allow`가 정확히 3개로
  좁힌다. `exec`, 파일, 웹, 메시지, 세션 도구는 모두 빠진다.
- `minimal` 프로필과 `alsoAllow` 조합은 쓰지 않는다. `minimal`이 MCP 도구를 숨기고,
  `alsoAllow`는 `session_status`, `gateway`를 남겨 "3개만"이 되지 않는다. 같은 범위에
  `allow`와 `alsoAllow`를 함께 쓰면 설정 검증에서 거부된다.
- `mcp.servers`는 전역 설정이라 다른 비서에도 보인다. 다른 비서에는 `deny`로 막는다.
  coordinator의 위임 연결은 다음 단계에서 정한다.

상위 정책에서 확인할 것:

- 전역 `tools.deny`에 `bundle-mcp`나 `factory-api__*`가 없어야 한다.
- 전역 `tools.allow`를 쓴다면 위 3개가 들어 있어야 한다.
- 샌드박스 모드를 켜면 샌드박스 도구 허용 목록에도 3개(또는 `factory-api__*`)를
  넣어야 한다.
- 제공자별 정책(`tools.byProvider`)을 쓴다면 같은 조건을 확인한다.

적용 후 확인 방법 **[미검증]**:

```powershell
openclaw config validate
openclaw mcp status --verbose
openclaw agent --agent factory-operations --message "오늘 입고된 계근기록을 조회해줘"
openclaw logs   # agents/tool-policy 항목에서 걸러진 도구 확인
```

### 4.5 실제 비서 검증 (온보딩 후)

- `factory-operations`가 대화에서 도구 3개를 호출해 답하는지.
- 비서에게 보이는 도구가 3개뿐인지, 다른 비서에게는 `factory-api__*`가 보이지 않는지.
- 없는 계근번호, 타임존 없는 날짜를 물었을 때 값을 지어내지 않고 오류를 알리는지.
- 이 검증은 개발 DB를 **읽기만** 한다.

## 5. 종료와 등록 해제

- MCP 서버는 Gateway가 자식 프로세스로 실행하고, Gateway를 멈추면 함께 정리된다
  (`docs/cli/mcp/registry.md`: 소유한 stdio 자식 프로세스는 정리 시 종료). **[미검증]**
- 서버는 stdin이 닫히면 스스로 종료한다(프로토콜 테스트에서 종료코드 0 확인).
- 포그라운드 Gateway(`openclaw gateway run`)는 Ctrl+C로 멈춘다. 예약 작업으로
  설치했다면 `Stop-ScheduledTask` 또는 `schtasks /end /tn "OpenClaw Gateway"`로
  멈춘다. **[미검증]**
- 일시 중지와 재개: `openclaw mcp configure factory-api --disable` / `--enable`
  (`openclaw mcp configure --help`에서 확인). 정의는 남기고 연결만 하지 않는다. **[미검증]**
- 등록 해제: `openclaw mcp unset factory-api`. **[미검증]**
- backend API는 별도 프로세스다. `uvicorn`을 실행한 터미널에서 Ctrl+C로 멈춘다.

## 6. 온보딩 때 결정할 항목

- **모델 제공자와 모델**: 도구 호출을 지원해야 한다. 계근 데이터(거래처명, 차량번호,
  중량)가 모델 제공자로 전송되므로, 외부 API를 쓸지 로컬 모델을 쓸지가 보안상 가장
  큰 결정이다.
- **인증 방식**: API 키, 구독 토큰, OAuth 중 선택. 키는 직접 입력하고 저장소에 넣지 않는다.
- **Gateway**: 인증 방식(토큰 또는 비밀번호), 필요할 때만 실행할지 예약 작업으로 설치할지.
- **건너뛸 항목**: 채널, 스킬, 웹 검색, 훅은 이번 단계에 필요 없다.
- **전역 `tools.profile`**: 온보딩이 설정하는 `"full"`을 그대로 둘지, 전역도 좁힐지.

## 7. 알려진 제한과 후속 작업

- **API 인증 없음**: 조회 API에 인증이 없다. 이번 단계는 `127.0.0.1` 전용 로컬
  조회 연결만 다룬다. API 인증과, MCP 서버가 인증정보를 전달하는 방식은 후속 작업이다.
- **호스트 제한**: `127.0.0.1`만 허용한다. OpenClaw를 WSL2나 Docker에서 실행하거나
  API를 다른 서버에 두려면 `config.py`의 허용 규칙을 바꿔야 한다.
- **집계 없음**: 도구는 기록을 조회만 한다. 중량 합계와 집계는 프로그램이 계산한
  결과만 보고한다는 원칙에 따라, 집계 API가 생기기 전까지 비서는 합계를 답하지 않고
  기능의 한계를 안내한다(`factory-operations/AGENTS.md`).
- **전이 의존성 미고정**: 잠금 파일이 없다.
- **SDK 호환성**: `mcp 2.3.0`의 저수준 `Server` API를 쓴다. 고수준 `MCPServer`는
  정의되지 않은 인자를 무시하고 스키마에 `additionalProperties: false`를 넣지 않아
  쓰지 않았다. OpenClaw 2026.9.8의 MCP 클라이언트와의 실제 연결은 등록 단계에서
  `doctor --probe`로 확인해야 한다. **[미검증]**
