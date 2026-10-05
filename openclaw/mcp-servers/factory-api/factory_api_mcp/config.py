"""API 기본 주소 설정.

기본 주소는 운영자가 환경변수로만 지정한다. 도구 인자로는 주소·호스트·경로를
받지 않는다. 이번 단계에서는 이 PC의 FastAPI(127.0.0.1)만 허용한다.
"""

import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

BASE_URL_ENV = "FACTORY_API_BASE_URL"
ALLOWED_HOST = "127.0.0.1"
_EXAMPLE = f"http://{ALLOWED_HOST}:8000"

# 최종 관문. 아래의 개별 검사는 사람이 읽을 오류 문구를 고르기 위한 것이고,
# 실제 허용 여부는 이 형식과 정확히 일치하는지로 결정한다.
_CANONICAL = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})")


class ConfigError(Exception):
    """기본 주소가 없거나 허용되지 않는 형식일 때. 메시지는 stderr에 그대로 출력한다."""


def validate_base_url(value: str) -> str:
    if value != value.strip() or any(ch.isspace() or ord(ch) < 0x20 for ch in value):
        raise ConfigError(f"{BASE_URL_ENV}에 공백이나 제어문자가 들어 있습니다.")
    if "\\" in value:
        raise ConfigError(f"{BASE_URL_ENV}에 역슬래시를 사용할 수 없습니다.")
    if "?" in value:
        raise ConfigError(f"{BASE_URL_ENV}에 쿼리(?)를 붙일 수 없습니다.")
    if "#" in value:
        raise ConfigError(f"{BASE_URL_ENV}에 fragment(#)를 붙일 수 없습니다.")

    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ConfigError(f"{BASE_URL_ENV} 형식이 올바르지 않습니다. 예: {_EXAMPLE}") from None

    if parts.scheme != "http":
        raise ConfigError(f"{BASE_URL_ENV}는 http:// 로 시작해야 합니다. 예: {_EXAMPLE}")
    if "@" in parts.netloc:
        raise ConfigError(f"{BASE_URL_ENV}에 계정정보(user:password@)를 넣을 수 없습니다.")
    if parts.path:
        raise ConfigError(f"{BASE_URL_ENV}에 경로를 붙일 수 없습니다(끝의 / 포함). 예: {_EXAMPLE}")
    if parts.hostname != ALLOWED_HOST:
        raise ConfigError(f"{BASE_URL_ENV}의 호스트는 {ALLOWED_HOST}만 허용합니다.")
    if port is None:
        raise ConfigError(f"{BASE_URL_ENV}에 포트를 명시해야 합니다. 예: {_EXAMPLE}")

    match = _CANONICAL.fullmatch(value)
    if match is None or not 1 <= int(match.group(1)) <= 65535:
        raise ConfigError(f"{BASE_URL_ENV} 형식이 올바르지 않습니다. 예: {_EXAMPLE}")
    return value


def load_base_url(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = env.get(BASE_URL_ENV)
    if not value:
        raise ConfigError(f"환경변수 {BASE_URL_ENV}가 설정되지 않았습니다. 예: {_EXAMPLE}")
    return validate_base_url(value)
