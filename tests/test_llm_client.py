"""Security and contract tests for the OpenAI-compatible LLM boundary."""

from __future__ import annotations

import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from helpers import llm_client
from helpers.llm_client import (
    ChatCompletionResult,
    LLMClientError,
    LLMConfigurationError,
    LLMResponseError,
    LLMTransientError,
    ToolCall,
    build_editorial_system_prompt,
    clean_json_response,
    generate_editorial_plan,
    get_llm_config,
    send_chat_completion,
    send_chat_completion_structured,
)


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.headers = headers or {}

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount < 0 else self.body[:amount]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def completion_body(
    *,
    content: str | None = "complete",
    tool_calls: list[dict[str, object]] | None = None,
    usage: dict[str, int] | None = None,
) -> bytes:
    message: dict[str, object] = {"content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return json.dumps(
        {
            "id": "body-request-id",
            "model": "provider-model",
            "choices": [
                {
                    "message": message,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }
    ).encode("utf-8")


def configured_client() -> dict[str, str]:
    return {
        "api_key": "unit-test-key",
        "base_url": "https://provider.example/v1",
        "model": "requested-model",
    }


def test_default_env_is_exact_trusted_root_and_workspace_cannot_hijack_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_root = tmp_path / "installed-runtime"
    trusted_root.mkdir()
    trusted_env = trusted_root / ".env"
    trusted_env.write_text(
        "LLM_API_KEY=trusted-key\n"
        "LLM_BASE_URL=https://trusted.example/v1\n"
        "LLM_MODEL=trusted-model\n",
        encoding="utf-8",
    )
    media_dir = tmp_path / "raw-media"
    media_dir.mkdir()
    (media_dir / ".env").write_text(
        "LLM_API_KEY=cwd-canary\nLLM_BASE_URL=https://cwd-attacker.example/v1\n",
        encoding="utf-8",
    )
    (media_dir / "alanocut.json").write_text(
        json.dumps(
            {
                "llm": {
                    "api_key": "workspace-canary",
                    "base_url": "https://workspace-attacker.example/v1",
                    "model": "attacker-model",
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(media_dir)
    monkeypatch.setattr(llm_client, "TRUSTED_ENV_PATH", trusted_env)
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    config = get_llm_config(workspace_dir=media_dir)

    assert config == {
        "api_key": "trusted-key",
        "base_url": "https://trusted.example/v1",
        "model": "trusted-model",
    }
    assert "canary" not in repr(config)


def test_process_environment_has_priority_over_trusted_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_env = tmp_path / ".env"
    trusted_env.write_text(
        "LLM_API_KEY=file-key\nLLM_BASE_URL=https://file.example/v1\nLLM_MODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_client, "TRUSTED_ENV_PATH", trusted_env)
    monkeypatch.setenv("LLM_API_KEY", "environment-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("LLM_MODEL", "environment-model")

    assert get_llm_config() == {
        "api_key": "environment-key",
        "base_url": "https://environment.example/v1",
        "model": "environment-model",
    }


@pytest.mark.parametrize(
    "base_url",
    [
        "https://remote.example/v1",
        "https://127.0.0.1:8443/v1",
        "http://localhost:11434/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
    ],
)
def test_provider_url_accepts_https_or_loopback_http(base_url: str) -> None:
    config = configured_client() | {"base_url": base_url}
    assert llm_client._validate_config(config)[1] == base_url


@pytest.mark.parametrize(
    "base_url",
    [
        "http://remote.example/v1",
        "http://192.168.1.10/v1",
        "https://user:password@remote.example/v1",
        "https://remote.example/v1?key=canary",
        "https://remote.example/v1#fragment",
        "ftp://remote.example/v1",
        "https://remote.example:invalid/v1",
        "https://remote.example\\@attacker.example/v1",
        "//remote.example/v1",
    ],
)
def test_provider_url_rejects_unsafe_envelopes(base_url: str) -> None:
    with pytest.raises(LLMConfigurationError, match="LLM_CONFIG_INVALID"):
        llm_client._validate_config(configured_client() | {"base_url": base_url})


def test_structured_completion_returns_tool_calls_usage_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        completion_body(
            content=None,
            tool_calls=[
                {
                    "id": "call-01",
                    "type": "function",
                    "function": {
                        "name": "write_artifact",
                        "arguments": '{"name":"diagnosis.json","expected_revision":0}',
                    },
                }
            ],
        ),
        headers={"x-request-id": "header-request-id"},
    )
    captured: dict[str, Any] = {}

    def fake_open(request: object, timeout_seconds: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout_seconds
        return response

    monkeypatch.setattr(llm_client, "_open_without_redirects", fake_open)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "write_artifact",
                "parameters": {"type": "object"},
            },
        }
    ]

    result = send_chat_completion_structured(
        messages=[{"role": "user", "content": "Do the task"}],
        config=configured_client(),
        tools=tools,
        tool_choice="auto",
        timeout_seconds=7,
    )

    assert isinstance(result, ChatCompletionResult)
    assert result.content is None
    assert result.tool_calls == (
        ToolCall(
            call_id="call-01",
            name="write_artifact",
            arguments={"name": "diagnosis.json", "expected_revision": 0},
        ),
    )
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    assert result.model == "provider-model"
    assert result.finish_reason == "tool_calls"
    assert result.request_id == "header-request-id"
    assert result.latency_ms >= 0
    assert captured["timeout"] == 7

    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"
    assert request.get_header("Authorization") == "Bearer unit-test-key"


def test_legacy_text_wrapper_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "_open_without_redirects",
        lambda request, timeout: FakeResponse(completion_body(content="legacy text")),
    )

    assert send_chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        config=configured_client(),
    ) == "legacy text"


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b'{"choices":[]}',
        b'{"choices":[{"message":null}]}',
        b'{"choices":[{"message":{"content":42}}]}',
        b'{"choices":[{"message":{"content":null,"tool_calls":[]}}]}',
        b'{"choices":[{"message":{"content":"a"}},{"message":{"content":"b"}}]}',
        b'{"choices":[{"message":{"content":"ok"}}],"usage":{"total_tokens":-1}}',
        b'{"choices":[{"message":{"content":"ok"}}],"usage":{"total_tokens":true}}',
        b'{"choices":[],"choices":[]}',
        b'{"choices":[{"message":{"content":"ok"}}],"value":NaN}',
    ],
)
def test_structured_completion_rejects_invalid_response_envelopes(
    body: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm_client,
        "_open_without_redirects",
        lambda request, timeout: FakeResponse(body),
    )

    with pytest.raises(LLMResponseError, match="LLM_RESPONSE_INVALID"):
        send_chat_completion_structured(
            messages=[{"role": "user", "content": "hello"}],
            config=configured_client(),
        )


@pytest.mark.parametrize(
    "arguments",
    [
        "[]",
        '{"path":"one","path":"two"}',
        '{"value":NaN}',
        "not-json",
    ],
)
def test_tool_call_arguments_use_strict_object_parser(
    arguments: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = completion_body(
        content=None,
        tool_calls=[
            {
                "id": "call-01",
                "type": "function",
                "function": {"name": "read_file", "arguments": arguments},
            }
        ],
    )
    monkeypatch.setattr(
        llm_client,
        "_open_without_redirects",
        lambda request, timeout: FakeResponse(body),
    )

    with pytest.raises(LLMResponseError, match="LLM_TOOL_ARGUMENTS_INVALID"):
        send_chat_completion_structured(
            messages=[{"role": "user", "content": "hello"}],
            config=configured_client(),
        )


def test_response_body_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_client, "MAX_RESPONSE_BYTES", 32)
    monkeypatch.setattr(
        llm_client,
        "_open_without_redirects",
        lambda request, timeout: FakeResponse(b"x" * 33),
    )

    with pytest.raises(LLMResponseError, match="LLM_RESPONSE_TOO_LARGE"):
        send_chat_completion_structured(
            messages=[{"role": "user", "content": "hello"}],
            config=configured_client(),
        )


def test_redirect_is_not_followed_and_secret_is_not_forwarded() -> None:
    state = {"redirect_target_called": False}

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - standard-library callback name
            if self.path == "/capture":
                state["redirect_target_called"] = True
                self.send_response(200)
                self.end_headers()
                return
            self.send_response(302)
            self.send_header("Location", "/capture")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - standard-library callback name
            state["redirect_target_called"] = True
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(LLMClientError, match="LLM_HTTP_ERROR.*302"):
            send_chat_completion_structured(
                messages=[{"role": "user", "content": "hello"}],
                config={
                    "api_key": "redirect-secret-canary",
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                    "model": "local-model",
                },
            )
        assert state["redirect_target_called"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
def test_transient_http_status_is_classified_without_client_retry_or_secret_leak(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    body_canary = "transient-body-secret-canary"
    header_canary = "transient-header-secret-canary"

    def fail_once(request: object, timeout: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            url="https://provider.example/v1/chat/completions",
            code=status_code,
            msg="transient-reason-secret-canary",
            hdrs={"x-provider-secret": header_canary},
            fp=io.BytesIO(body_canary.encode("utf-8")),
        )

    monkeypatch.setattr(llm_client, "_open_without_redirects", fail_once)

    with pytest.raises(LLMTransientError) as caught:
        send_chat_completion_structured(
            messages=[{"role": "user", "content": "hello"}],
            config=configured_client(),
        )

    assert calls == 1
    assert str(caught.value) == (
        f"LLM_TRANSIENT_HTTP_ERROR: provider returned HTTP {status_code}"
    )
    assert body_canary not in str(caught.value)
    assert header_canary not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_permanent_http_status_is_not_classified_as_transient(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm_client,
        "_open_without_redirects",
        lambda request, timeout: (_ for _ in ()).throw(
            urllib.error.HTTPError(
                url="https://provider.example/v1/chat/completions",
                code=status_code,
                msg="permanent-reason-secret-canary",
                hdrs={},
                fp=io.BytesIO(b"permanent-body-secret-canary"),
            )
        ),
    )

    with pytest.raises(LLMClientError) as caught:
        send_chat_completion_structured(
            messages=[{"role": "user", "content": "hello"}],
            config=configured_client(),
        )

    assert not isinstance(caught.value, LLMTransientError)
    assert str(caught.value) == f"LLM_HTTP_ERROR: provider returned HTTP {status_code}"
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "transport_error",
    [
        urllib.error.URLError("transport-reason-secret-canary"),
        TimeoutError("timeout-reason-secret-canary"),
        OSError("socket-reason-secret-canary"),
    ],
    ids=["url-error", "timeout", "os-error"],
)
def test_transport_failure_is_transient_and_content_free(
    transport_error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_once(request: object, timeout: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        raise transport_error

    monkeypatch.setattr(llm_client, "_open_without_redirects", fail_once)

    with pytest.raises(LLMTransientError) as caught:
        send_chat_completion_structured(
            messages=[{"role": "user", "content": "hello"}],
            config=configured_client(),
        )

    assert calls == 1
    assert str(caught.value) == "LLM_TRANSIENT_TRANSPORT_ERROR: provider request failed"
    assert "canary" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_http_error_does_not_expose_provider_body_reason_or_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_canary = "provider-response-secret-canary"
    reason_canary = "provider-reason-secret-canary"
    api_key_canary = "provider-api-key-secret-canary"
    http_error = urllib.error.HTTPError(
        url="https://provider.example/v1/chat/completions",
        code=401,
        msg=reason_canary,
        hdrs={},
        fp=io.BytesIO(response_canary.encode("utf-8")),
    )
    monkeypatch.setattr(
        llm_client,
        "_open_without_redirects",
        lambda request, timeout: (_ for _ in ()).throw(http_error),
    )

    with pytest.raises(LLMClientError) as caught:
        send_chat_completion_structured(
            messages=[{"role": "user", "content": "hello"}],
            config=configured_client() | {"api_key": api_key_canary},
        )

    public_error = str(caught.value)
    assert public_error == "LLM_HTTP_ERROR: provider returned HTTP 401"
    assert response_canary not in public_error
    assert reason_canary not in public_error
    assert api_key_canary not in public_error
    assert caught.value.__cause__ is None


def test_invalid_response_error_does_not_echo_raw_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_canary = "invalid-response-secret-canary"
    monkeypatch.setattr(
        llm_client,
        "_open_without_redirects",
        lambda request, timeout: FakeResponse(response_canary.encode("utf-8")),
    )

    with pytest.raises(LLMResponseError) as caught:
        send_chat_completion_structured(
            messages=[{"role": "user", "content": "hello"}],
            config=configured_client(),
        )

    assert response_canary not in str(caught.value)
    assert caught.value.__cause__ is None


def test_clean_json_response_handles_markdown_and_think_tags() -> None:
    raw = '<think>private chain</think>\n```json\n[{"source":"C001","start":1,"end":5}]\n```'
    assert json.loads(clean_json_response(raw))[0]["source"] == "C001"


def test_build_editorial_system_prompt_short_vs_long_form() -> None:
    assert "<= 90 segundos" in build_editorial_system_prompt("reels")
    assert "500ms" in build_editorial_system_prompt("aula")


def test_generate_editorial_plan_preserves_legacy_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm_client,
        "send_chat_completion",
        lambda **kwargs: json.dumps(
            [
                {"source": "C001", "start": 10, "end": 20, "beat": "HOOK"},
                {"source": "C001", "start": 30, "end": 25},
            ]
        ),
    )

    cuts = generate_editorial_plan("brief", "takes")

    assert cuts == [
        {
            "source": "C001",
            "start": 10.0,
            "end": 20.0,
            "beat": "HOOK",
            "quote": "",
            "reason": "",
            "is_list": False,
        }
    ]


def test_legacy_plan_parse_error_does_not_echo_raw_model_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_canary = "raw-model-secret-canary"
    monkeypatch.setattr(llm_client, "send_chat_completion", lambda **kwargs: model_canary)

    with pytest.raises(ValueError) as caught:
        generate_editorial_plan("brief", "takes")

    assert model_canary not in str(caught.value)
