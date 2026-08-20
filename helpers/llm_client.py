"""Universal OpenAI-compatible LLM client for Alano Rough Cut AI.

Supports NVIDIA NIM, Ollama, vLLM, OpenAI, Groq, and local inference servers.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def load_env_file(env_path: Path | None = None) -> None:
    """Load key-value pairs from .env file into os.environ if not already set."""
    if env_path is None:
        # Search current working directory and upwards
        curr = Path.cwd().resolve()
        for _ in range(6):
            candidate = curr / ".env"
            if candidate.exists():
                env_path = candidate
                break
            if curr.parent == curr:
                break
            curr = curr.parent

        if env_path is None:
            # Search file directory and upwards
            curr = Path(__file__).resolve().parent
            for _ in range(6):
                candidate = curr / ".env"
                if candidate.exists():
                    env_path = candidate
                    break
                if curr.parent == curr:
                    break
                curr = curr.parent

    if env_path and env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass


def get_llm_config(workspace_dir: Path | None = None) -> dict[str, str]:
    """Resolve LLM configuration from environment variables or workspace settings."""
    load_env_file()

    # Default values
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    base_url = os.environ.get("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1").strip().rstrip("/")
    model = os.environ.get("LLM_MODEL", "z-ai/glm-5.2").strip()

    # Check workspace settings file (alanocut.json) if present
    if workspace_dir:
        settings_file = workspace_dir / "alanocut.json"
        if settings_file.exists():
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                llm_cfg = data.get("llm", {})
                if llm_cfg.get("api_key"):
                    api_key = str(llm_cfg["api_key"]).strip()
                if llm_cfg.get("base_url"):
                    base_url = str(llm_cfg["base_url"]).strip().rstrip("/")
                if llm_cfg.get("model"):
                    model = str(llm_cfg["model"]).strip()
            except Exception:
                pass

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }


def clean_json_response(raw_text: str) -> str:
    """Extract clean JSON from LLM response containing markdown codeblocks or thoughts."""
    text = raw_text.strip()

    # Strip reasoning / thoughts blocks if present (e.g. <think>...</think>)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Extract content inside markdown ```json ... ``` or ``` ... ```
    json_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if json_block:
        text = json_block.group(1).strip()

    # If the text has surrounding non-JSON characters, find the first '[' or '{' and last ']' or '}'
    first_bracket = min(
        [i for i in [text.find("["), text.find("{")] if i != -1] or [-1]
    )
    last_bracket = max(
        [i for i in [text.rfind("]"), text.rfind("}")] if i != -1] or [-1]
    )

    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        text = text[first_bracket : last_bracket + 1]

    return text


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def get_editor_system_prompt_template() -> str:
    """Load editor system prompt template from dedicated Markdown file."""
    prompt_file = PROMPTS_DIR / "editor_system_prompt.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")
    
    # Fallback to AppData install location if running from custom directory
    appdata_prompt = (
        Path(os.environ.get("APPDATA", ""))
        / "alano-rought-cut-ai"
        / "helpers"
        / "prompts"
        / "editor_system_prompt.md"
    )
    if appdata_prompt.exists():
        return appdata_prompt.read_text(encoding="utf-8")
        
    raise FileNotFoundError(f"editor_system_prompt.md not found at {prompt_file} or {appdata_prompt}")


def build_editorial_system_prompt(video_type: str = "aula") -> str:
    """Build high-performance system prompt with autonomous senior editorial intelligence."""
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

    template = get_editor_system_prompt_template()
    return template.replace("{pacing_rules}", pacing_rules)


def generate_editorial_plan(
    brief: str,
    takes_packed_content: str,
    video_type: str = "aula",
    config: dict[str, str] | None = None,
    timeout_seconds: int = 90,
) -> list[dict[str, Any]]:
    """Send structured editorial prompt to OpenAI-compatible LLM and return parsed cut list."""
    if config is None:
        config = get_llm_config()

    api_key = config.get("api_key", "")
    base_url = config.get("base_url", "https://integrate.api.nvidia.com/v1").rstrip("/")
    model = config.get("model", "z-ai/glm-5.2")

    if not api_key:
        raise ValueError(
            "LLM API Key is missing. Set LLM_API_KEY in your .env file or environment."
        )

    system_prompt = build_editorial_system_prompt(video_type=video_type)
    user_prompt = f"""BRIEFING DO USUÁRIO:
{brief}

TRANSCRIÇÕES AGRUPADAS (takes_packed.md):
{takes_packed_content}

Analise os takes e retorne o JSON array ordenado com o plano de corte ideal."""

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 3000,
    }

    endpoint = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            resp_body = resp.read().decode("utf-8")
            data = json.loads(resp_body)
            raw_content = data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"LLM API Error (HTTP {e.code}): {e.reason}\nDetails: {err_text}"
        ) from e
    except Exception as e:
        raise RuntimeError(f"Failed to communicate with LLM API: {e}") from e

    cleaned_json = clean_json_response(raw_content)
    try:
        cuts = json.loads(cleaned_json)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM did not return valid JSON:\nRaw response:\n{raw_content}\nCleaned:\n{cleaned_json}"
        ) from e

    if not isinstance(cuts, list):
        raise ValueError(f"Expected a JSON list of cut objects, got {type(cuts)}")

    # Validate each cut object
    validated_cuts = []
    for i, cut in enumerate(cuts):
        if not isinstance(cut, dict):
            continue
        source = cut.get("source")
        start = cut.get("start")
        end = cut.get("end")
        if not source or start is None or end is None:
            continue
        try:
            start_f = float(start)
            end_f = float(end)
            if end_f <= start_f:
                continue
        except (ValueError, TypeError):
            continue

        validated_cuts.append({
            "source": str(source).strip(),
            "start": start_f,
            "end": end_f,
            "beat": str(cut.get("beat") or f"BEAT_{i+1}"),
            "quote": str(cut.get("quote") or ""),
            "reason": str(cut.get("reason") or ""),
            "is_list": bool(cut.get("is_list", False)),
        })

    if not validated_cuts:
        raise ValueError("No valid cut ranges were extracted from LLM response.")

    return validated_cuts
