"""실행 진입점: python -m factory_api_mcp

stdout은 MCP 메시지 전용이다. 로그와 오류 안내는 모두 stderr로 보낸다.
"""

import logging
import sys

import anyio

from factory_api_mcp.config import ConfigError, load_base_url
from factory_api_mcp.server import run_stdio

EXIT_CONFIG_ERROR = 2


def main() -> int:
    # Windows에서 파이프로 연결된 stderr는 기본 인코딩이 cp949라 한글 로그가 깨진다.
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        base_url = load_base_url()
    except ConfigError as exc:
        print(f"factory-api MCP 서버를 시작할 수 없습니다: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    anyio.run(run_stdio, base_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
