"""FACTORY_API_BASE_URL 검증."""

import pytest

from factory_api_mcp.config import BASE_URL_ENV, ConfigError, load_base_url, validate_base_url


@pytest.mark.parametrize("value", ["http://127.0.0.1:8000", "http://127.0.0.1:1", "http://127.0.0.1:65535"])
def test_accepts_loopback_with_explicit_port(value):
    assert validate_base_url(value) == value


def test_load_reads_environment():
    assert load_base_url({BASE_URL_ENV: "http://127.0.0.1:54321"}) == "http://127.0.0.1:54321"


@pytest.mark.parametrize("environ", [{}, {BASE_URL_ENV: ""}])
def test_missing_value_is_refused_with_clear_message(environ):
    with pytest.raises(ConfigError, match=BASE_URL_ENV):
        load_base_url(environ)


@pytest.mark.parametrize(
    "value",
    [
        # 호스트: 127.0.0.1만 허용
        "http://localhost:8000",
        "http://host.docker.internal:8000",
        "http://0.0.0.0:8000",
        "http://127.0.0.2:8000",
        "http://127.1:8000",
        "http://2130706433:8000",
        "http://[::1]:8000",
        "http://192.168.0.10:8000",
        "http://example.com:8000",
        "http://127.0.0.1.example.com:8000",
        # 스킴
        "https://127.0.0.1:8000",
        "ftp://127.0.0.1:8000",
        "127.0.0.1:8000",
        "HTTP://127.0.0.1:8000",
        # 계정정보
        "http://user:pass@127.0.0.1:8000",
        "http://user@127.0.0.1:8000",
        "http://127.0.0.1:8000@example.com",
        # 경로, 쿼리, fragment
        "http://127.0.0.1:8000/",
        "http://127.0.0.1:8000/api/v1",
        "http://127.0.0.1:8000?x=1",
        "http://127.0.0.1:8000?",
        "http://127.0.0.1:8000#frag",
        "http://127.0.0.1:8000#",
        # 포트
        "http://127.0.0.1",
        "http://127.0.0.1:",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:08000",
        "http://127.0.0.1:80a",
        # 공백, 제어문자, 역슬래시
        " http://127.0.0.1:8000",
        "http://127.0.0.1:8000 ",
        "http://127.0.0.1:8000\n",
        "http://127.0.0.1:8000\\@example.com",
    ],
)
def test_rejects_anything_but_plain_loopback(value):
    with pytest.raises(ConfigError):
        validate_base_url(value)


def test_error_message_does_not_echo_credentials():
    with pytest.raises(ConfigError) as excinfo:
        validate_base_url("http://admin:s3cret@127.0.0.1:8000")
    assert "s3cret" not in str(excinfo.value)
