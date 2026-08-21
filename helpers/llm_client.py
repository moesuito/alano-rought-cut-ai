"""Hardened OpenAI-compatible LLM client for Alano Rough Cut AI.

The structured client is the canonical boundary for remote and local models.
``send_chat_completion`` remains as an explicit text-only compatibility wrapper
for the legacy three-phase editor.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, TypeGuard


DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "z-ai/glm-5.2"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
TRUSTED_ROOT = Path(__file__).resolve().parents[1]
TRUSTED_ENV_PATH = TRUSTED_ROOT / ".env"


class LLMClientError(RuntimeError):
    """Stable, content-free error raised at the provider boundary."""


class LLMTransientError(LLMClientError):
    """A provider failure that the agent runner may safely retry."""


class LLMConfigurationError(LLMClientError):
    """The trusted LLM configuration is missing or unsafe."""


class LLMResponseError(LLMClientError):
    """The provider response does not satisfy the supported contract."""


@dataclass(frozen=True)
class ToolCall:
    """A validated function call requested by an OpenAI-compatible model."""

    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ChatCompletionResult:
    """Validated response data needed by the agent runtime and telemetry."""

    content: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: dict[str, int]
    model: str
    finish_reason: str | None
    request_id: str | None
    latency_ms: int


class _Headers(Protocol):
    def get(self, name: str, default: str | None = None) -> str | None: ...


class _ReadableResponse(Protocol):
    headers: _Headers

    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> _ReadableResponse: ...

    def __exit__(self, *args: object) -> object: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into an HTTP error without forwarding credentials."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def load_env_file(env_path: Path | None = None) -> None:
    """Load a trusted .env without overriding process environment variables.

    The default is exactly the checkout/install root containing this module.
    It never searches the media workspace, current directory, or parent paths.
    An explicit path is retained for controlled setup and unit-test callers.
    """

    selected_path = TRUSTED_ENV_PATH if env_path is None else Path(env_path)
    if not selected_path.is_file():
        return

    try:
        lines = selected_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'\"")


def get_llm_config(workspace_dir: Path | None = None) -> dict[str, str]:
    """Resolve provider configuration only from trusted .env and environment.

    ``workspace_dir`` is accepted solely for source compatibility. Workspace
    ``alanocut.json`` files are intentionally not a provider configuration
    source because raw-media folders are untrusted input.
    """

    del workspace_dir
    load_env_file()

    api_key = os.environ.get("LLM_API_KEY", "").strip()
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL).strip()
    validated_base_url = _validate_base_url(base_url)
    if not model:
        raise LLMConfigurationError("LLM_CONFIG_INVALID: model is required")

    return {
        "api_key": api_key,
        "base_url": validated_base_url,
        "model": model,
    }


def clean_json_response(raw_text: str) -> str:
    """Extract JSON text from legacy Markdown or thinking wrappers."""

    text = raw_text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    json_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if json_block:
        text = json_block.group(1).strip()

    first_bracket = min([i for i in (text.find("["), text.find("{")) if i != -1] or [-1])
    last_bracket = max([i for i in (text.rfind("]"), text.rfind("}")) if i != -1] or [-1])
    if first_bracket != -1 and last_bracket > first_bracket:
        text = text[first_bracket : last_bracket + 1]
    return text


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def get_editor_system_prompt_template() -> str:
    """Load the legacy one-shot system prompt from the installed runtime."""

    prompt_file = PROMPTS_DIR / "editor_system_prompt.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")

    appdata_prompt = (
        Path(os.environ.get("APPDATA", ""))
        / "alano-rought-cut-ai"
        / "helpers"
        / "prompts"
        / "editor_system_prompt.md"
    )
    if appdata_prompt.exists():
        return appdata_prompt.read_text(encoding="utf-8")
    raise FileNotFoundError("editor_system_prompt.md is unavailable in the trusted runtime")


def build_editorial_system_prompt(video_type: str = "aula") -> str:
    """Build the legacy one-shot editorial system prompt."""

    is_short = video_type.lower() in {"reels", "tiktok", "shorts", "social", "short"}
    if is_short:
        pacing_rules = (
            "- FORMATO: VÍDEO CURTO / REDES SOCIAIS (Reels / TikTok / YouTube Shorts).\n"
            "- OBJETIVO: Ritmo acelerado (fast-pacing), gancho magnético nos primeiros 3 segundos, cortes dinâmicos e sem respiros mortos.\n"
            "- DURAÇÃO MÁXIMA: Estritamente <= 90 segundos (ideal entre 30s e 60s).\n"
            "- LISTAS: Não aplicar preservação de lista ('is_list': false); cortes rápidos e diretos ao ponto."
        )
    else:
        pacing_rules = (
            "- FORMATO: VÍDEO LONGO / EDUCACIONAL (Videoaula / Tutorial / Curso / YouTube).\n"
            "- OBJETIVO: Didático, fluido, natural, cadenciado e completo, priorizando a clareza e a retenção do aluno.\n"
            "- LISTAS E ENUMERAÇÕES: Quando o apresentador listar recursos, passos ou perfis (ex: 'conta, produtos, vendas...', 'produtores, afiliados, compradores...'), "
            "marque 'is_list': true para preservar as micropausas naturais de respiração e cadência didática (até 500ms).\n"
        )
    return get_editor_system_prompt_template().replace("{pacing_rules}", pacing_rules)


def send_chat_completion_structured(
    messages: Sequence[Mapping[str, object]],
    config: Mapping[str, str] | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout_seconds: float = 1200,
    tools: Sequence[Mapping[str, object]] | None = None,
    tool_choice: str | Mapping[str, object] | None = None,
) -> ChatCompletionResult:
    """Send one bounded request and return a strictly validated result."""

    resolved_config = get_llm_config() if config is None else dict(config)
    api_key, base_url, model = _validate_config(resolved_config)
    _validate_request_options(temperature, max_tokens, timeout_seconds)

    payload: dict[str, object] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools is not None:
        payload["tools"] = list(tools)
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=_encode_request(payload),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with _open_without_redirects(request, timeout_seconds) as response:
            response_body = _read_bounded_response(response)
            request_id = _response_request_id(response)
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        exc.close()
        if status_code in TRANSIENT_HTTP_STATUS_CODES:
            raise LLMTransientError(
                f"LLM_TRANSIENT_HTTP_ERROR: provider returned HTTP {status_code}"
            ) from None
        raise LLMClientError(f"LLM_HTTP_ERROR: provider returned HTTP {status_code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LLMTransientError(
            "LLM_TRANSIENT_TRANSPORT_ERROR: provider request failed"
        ) from None

    latency_ms = max(0, round((time.monotonic() - started) * 1000))
    response_data = _parse_json_object(response_body, "LLM_RESPONSE_INVALID")
    return _parse_completion(response_data, model, request_id, latency_ms)


def send_chat_completion(
    messages: list[dict[str, str]],
    config: dict[str, str] | None = None,
    temperature: float = 0.1,
    max_tokens: int = 3500,
    timeout_seconds: int = 120,
) -> str:
    """Legacy text-only wrapper around the canonical structured client."""

    result = send_chat_completion_structured(
        messages=messages,
        config=config,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    if result.content is None:
        raise LLMResponseError("LLM_RESPONSE_INVALID: text content is required")
    return result.content


def generate_editorial_plan(
    brief: str,
    takes_packed_content: str,
    video_type: str = "aula",
    config: dict[str, str] | None = None,
    timeout_seconds: int = 90,
) -> list[dict[str, Any]]:
    """Run the legacy one-shot plan and validate its cut-list envelope."""

    messages = [
        {"role": "system", "content": build_editorial_system_prompt(video_type)},
        {
            "role": "user",
            "content": (
                f"BRIEFING DO USUÁRIO:\n{brief}\n\n"
                f"TRANSCRIÇÕES AGRUPADAS (takes_packed.md):\n{takes_packed_content}\n\n"
                "Analise os takes e retorne o JSON array ordenado com o plano de corte ideal."
            ),
        },
    ]
    raw_content = send_chat_completion(
        messages=messages,
        config=config,
        timeout_seconds=timeout_seconds,
    )

    try:
        cuts = _parse_json_document(clean_json_response(raw_content))
    except (UnicodeError, ValueError):
        raise ValueError("LLM did not return a valid editorial cut list") from None
    if not isinstance(cuts, list):
        raise ValueError("LLM did not return an editorial cut list")

    validated_cuts: list[dict[str, Any]] = []
    for index, cut in enumerate(cuts):
        if not isinstance(cut, dict):
            continue
        source, start, end = cut.get("source"), cut.get("start"), cut.get("end")
        if not source or start is None or end is None:
            continue
        try:
            start_value, end_value = float(start), float(end)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(start_value) or not math.isfinite(end_value) or end_value <= start_value:
            continue
        validated_cuts.append(
            {
                "source": str(source).strip(),
                "start": start_value,
                "end": end_value,
                "beat": str(cut.get("beat") or f"BEAT_{index + 1}"),
                "quote": str(cut.get("quote") or ""),
                "reason": str(cut.get("reason") or ""),
                "is_list": bool(cut.get("is_list", False)),
            }
        )
    if not validated_cuts:
        raise ValueError("No valid cut ranges were extracted from the LLM response")
    return validated_cuts


def _validate_config(config: Mapping[str, str]) -> tuple[str, str, str]:
    api_key = str(config.get("api_key", "")).strip()
    if not api_key:
        raise LLMConfigurationError("LLM_CONFIG_INVALID: API key is required")
    base_url = _validate_base_url(str(config.get("base_url", DEFAULT_BASE_URL)).strip().rstrip("/"))
    model = str(config.get("model", DEFAULT_MODEL)).strip()
    if not model:
        raise LLMConfigurationError("LLM_CONFIG_INVALID: model is required")
    return api_key, base_url, model


def _validate_base_url(base_url: str) -> str:
    if not base_url or "\\" in base_url or any(character.isspace() for character in base_url):
        raise LLMConfigurationError("LLM_CONFIG_INVALID: base URL is unsafe")
    try:
        parsed = urllib.parse.urlsplit(base_url)
        _ = parsed.port
    except ValueError:
        raise LLMConfigurationError("LLM_CONFIG_INVALID: base URL is unsafe") from None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise LLMConfigurationError("LLM_CONFIG_INVALID: base URL is unsafe")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise LLMConfigurationError("LLM_CONFIG_INVALID: base URL is unsafe")
    if parsed.scheme.lower() == "http" and not _is_loopback_host(parsed.hostname):
        raise LLMConfigurationError("LLM_CONFIG_INVALID: HTTP is limited to loopback")
    return base_url


def _is_loopback_host(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def _validate_request_options(temperature: float, max_tokens: int | None, timeout_seconds: float) -> None:
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
        raise LLMConfigurationError("LLM_CONFIG_INVALID: temperature must be finite")
    if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0):
        raise LLMConfigurationError("LLM_CONFIG_INVALID: max_tokens must be positive")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise LLMConfigurationError("LLM_CONFIG_INVALID: timeout must be positive")


def _encode_request(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise LLMConfigurationError("LLM_REQUEST_INVALID: payload is not valid JSON") from None


def _open_without_redirects(
    request: urllib.request.Request,
    timeout_seconds: float,
) -> _ReadableResponse:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout_seconds)


def _read_bounded_response(response: _ReadableResponse) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
            if declared_length < 0:
                raise ValueError
            if declared_length > MAX_RESPONSE_BYTES:
                raise LLMResponseError("LLM_RESPONSE_TOO_LARGE: response exceeds limit")
        except ValueError:
            raise LLMResponseError("LLM_RESPONSE_INVALID: invalid content length") from None
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if not isinstance(body, bytes):
        raise LLMResponseError("LLM_RESPONSE_INVALID: response body must be bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise LLMResponseError("LLM_RESPONSE_TOO_LARGE: response exceeds limit")
    return body


def _response_request_id(response: _ReadableResponse) -> str | None:
    for name in ("x-request-id", "request-id", "x-correlation-id"):
        value = response.headers.get(name)
        if _is_safe_metadata(value, 256):
            return value
    return None


def _parse_completion(
    data: dict[str, object],
    requested_model: str,
    header_request_id: str | None,
    latency_ms: int,
) -> ChatCompletionResult:
    choices = data.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise LLMResponseError("LLM_RESPONSE_INVALID: exactly one choice is required")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise LLMResponseError("LLM_RESPONSE_INVALID: message is required")

    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise LLMResponseError("LLM_RESPONSE_INVALID: content must be text or null")
    tool_calls = _parse_tool_calls(message.get("tool_calls", []))

    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not _is_safe_metadata(finish_reason, 128):
        raise LLMResponseError("LLM_RESPONSE_INVALID: finish_reason is invalid")
    if content is None and not tool_calls:
        if finish_reason not in {"length", "content_filter"} and "filter" not in str(finish_reason).lower():
            raise LLMResponseError("LLM_RESPONSE_INVALID: content or tool calls are required")
    response_model = data.get("model", requested_model)
    if not _is_safe_metadata(response_model, 512):
        raise LLMResponseError("LLM_RESPONSE_INVALID: model is invalid")

    body_request_id = data.get("id")
    if not _is_safe_metadata(body_request_id, 256):
        body_request_id = None
    return ChatCompletionResult(
        content=content,
        tool_calls=tool_calls,
        usage=_parse_usage(data.get("usage")),
        model=response_model,
        finish_reason=finish_reason,
        request_id=header_request_id or body_request_id,
        latency_ms=latency_ms,
    )


def _parse_tool_calls(value: object) -> tuple[ToolCall, ...]:
    if not isinstance(value, list):
        raise LLMResponseError("LLM_RESPONSE_INVALID: tool_calls must be a list")
    parsed_calls: list[ToolCall] = []
    call_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or item.get("type") != "function":
            raise LLMResponseError("LLM_RESPONSE_INVALID: tool call is invalid")
        call_id, function = item.get("id"), item.get("function")
        if not _is_safe_metadata(call_id, 256) or call_id in call_ids or not isinstance(function, dict):
            raise LLMResponseError("LLM_RESPONSE_INVALID: tool call is invalid")
        name, arguments_data = function.get("name"), function.get("arguments")
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) is None
        ):
            raise LLMResponseError("LLM_RESPONSE_INVALID: tool function name is invalid")
        if isinstance(arguments_data, dict):
            arguments = arguments_data
        elif isinstance(arguments_data, str):
            try:
                encoded_arguments = arguments_data.encode("utf-8")
            except UnicodeError:
                raise LLMResponseError("LLM_TOOL_ARGUMENTS_INVALID: JSON object is invalid") from None
            arguments = _parse_json_object(encoded_arguments, "LLM_TOOL_ARGUMENTS_INVALID")
        else:
            raise LLMResponseError("LLM_RESPONSE_INVALID: tool function arguments are invalid")
        parsed_calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments))
        call_ids.add(call_id)
    return tuple(parsed_calls)


def _parse_usage(value: object) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LLMResponseError("LLM_RESPONSE_INVALID: usage must be an object")
    usage: dict[str, int] = {}
    for key, count in value.items():
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", key) is None
        ):
            raise LLMResponseError("LLM_RESPONSE_INVALID: usage value is invalid")
        if isinstance(count, dict):
            continue
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise LLMResponseError("LLM_RESPONSE_INVALID: usage value is invalid")
        usage[key] = count
    return usage


def _is_safe_metadata(value: object, max_length: int) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_length
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _parse_json_object(raw: bytes, error_code: str) -> dict[str, object]:
    try:
        value = _parse_json_document(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise LLMResponseError(f"{error_code}: JSON object is invalid") from None
    if not isinstance(value, dict):
        raise LLMResponseError(f"{error_code}: JSON object is required")
    return value


def _parse_json_document(raw_text: str) -> object:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_non_finite(value: str) -> object:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(
        raw_text,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_finite,
    )
