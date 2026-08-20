"""Interactive, reusable setup wizard for Alano Cut transcription profiles.

PowerShell owns bootstrap/update mechanics. This module owns all provider
choices so install, configure, and workspace initialization follow the same
auditable path without duplicating credential logic.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Sequence

try:
    from helpers.transcription_settings import (
        DIARIZATION_COMMUNITY_1,
        DIARIZATION_NONE,
        PROVIDER_ASSEMBLYAI,
        PROVIDER_ELEVENLABS,
        PROVIDER_VULKAN,
        SettingsError,
        TranscriptionSettings,
        global_env_path,
        read_settings,
        user_settings_path,
        workspace_settings_path,
        write_settings_atomic,
    )
    from helpers.gpu_detection import detect_recommended_runtime
except ModuleNotFoundError as exc:
    if exc.name != "helpers":
        raise
    from transcription_settings import (  # type: ignore[no-redef]
        DIARIZATION_COMMUNITY_1,
        DIARIZATION_NONE,
        PROVIDER_ASSEMBLYAI,
        PROVIDER_ELEVENLABS,
        PROVIDER_VULKAN,
        SettingsError,
        TranscriptionSettings,
        global_env_path,
        read_settings,
        user_settings_path,
        workspace_settings_path,
        write_settings_atomic,
    )
    from gpu_detection import detect_recommended_runtime  # type: ignore[no-redef]


class WizardError(RuntimeError):
    """A provider cannot be safely configured."""


def _read_env_value(name: str, path: Path | None = None) -> str | None:
    direct = os.environ.get(name, "").strip()
    if direct:
        return direct
    candidate = path or global_env_path()
    if not candidate.is_file():
        return None
    for raw in candidate.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split("=", 1)
        if key.strip() == name and value.strip().strip('"').strip("'"):
            return value.strip().strip('"').strip("'")
    return None


def _set_env_value(name: str, value: str, path: Path | None = None) -> None:
    if "\r" in value or "\n" in value:
        raise WizardError("credenciais não podem conter quebras de linha")
    if not value.strip():
        raise WizardError("credencial vazia")
    destination = path or global_env_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = destination.read_text(encoding="utf-8-sig").splitlines() if destination.exists() else []
    found = False
    new_lines: list[str] = []
    for raw in existing:
        if "=" in raw and not raw.lstrip().startswith("#"):
            key, _ = raw.split("=", 1)
            if key.strip() == name:
                new_lines.append(f"{name}={value.strip()}")
                found = True
                continue
        new_lines.append(raw)
    if not found:
        new_lines.append(f"{name}={value.strip()}")
    destination.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def choose(
    prompt: str,
    options: Sequence[tuple[str, str]],
    default: int = 0,
    *,
    input_func: Callable[[str], str] = input,
    non_interactive: bool = False,
) -> int:
    """Render an accessible [ ]-style single choice selector."""
    if not options:
        raise ValueError("options must not be empty")
    if non_interactive:
        return max(0, min(default, len(options) - 1))
    print(prompt)
    for index, (title, description) in enumerate(options, start=1):
        marker = "[*]" if (index - 1) == default else "[ ]"
        print(f"  {index}. {marker} {title}")
        if description:
            print(f"         {description}")
    while True:
        try:
            raw = input_func(f"Escolha uma opção [1-{len(options)}] (padrão: {default + 1}): ").strip()
        except EOFError:
            return default
        if not raw:
            return default
        try:
            val = int(raw)
            if 1 <= val <= len(options):
                return val - 1
        except ValueError:
            pass
        print(f"Opção inválida. Digite um número de 1 a {len(options)}.")


def validate_elevenlabs_key(api_key: str) -> None:
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/user",
        headers={"xi-api-key": api_key, "User-Agent": "AlanoCut-Setup/0.4"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                raise WizardError(f"ElevenLabs retornou HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise WizardError(f"chave ElevenLabs inválida (HTTP {exc.code})") from exc
    except Exception as exc:
        raise WizardError(f"não foi possível validar a chave ElevenLabs: {exc}") from exc


def validate_assemblyai_key(api_key: str) -> None:
    req = urllib.request.Request(
        "https://api.assemblyai.com/v2/account",
        headers={"authorization": api_key, "User-Agent": "AlanoCut-Setup/0.4"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                raise WizardError(f"AssemblyAI retornou HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise WizardError(f"chave AssemblyAI inválida (HTTP {exc.code})") from exc
    except Exception as exc:
        raise WizardError(f"não foi possível validar a chave AssemblyAI: {exc}") from exc


def provision(
    settings: TranscriptionSettings,
    *,
    non_interactive: bool = False,
) -> None:
    if settings.provider == PROVIDER_ELEVENLABS:
        key = _read_env_value("ELEVENLABS_API_KEY")
        if not key:
            if non_interactive:
                raise WizardError("ELEVENLABS_API_KEY é obrigatória no modo não interativo")
            key = getpass.getpass("Cole a ElevenLabs API Key (entrada oculta): ").strip()
        if not key:
            raise WizardError("nenhuma API key da ElevenLabs foi informada")
        if not os.environ.get("ALANOCUT_SKIP_CREDENTIAL_VALIDATION"):
            validate_elevenlabs_key(key)
        _set_env_value("ELEVENLABS_API_KEY", key)
        return

    if settings.provider == PROVIDER_ASSEMBLYAI:
        key = _read_env_value("ASSEMBLYAI_API_KEY")
        if not key:
            if non_interactive:
                raise WizardError("ASSEMBLYAI_API_KEY é obrigatória no modo não interativo")
            key = getpass.getpass("Cole a AssemblyAI API Key (entrada oculta): ").strip()
        if not key:
            raise WizardError("nenhuma API key da AssemblyAI foi informada")
        if not os.environ.get("ALANOCUT_SKIP_CREDENTIAL_VALIDATION"):
            validate_assemblyai_key(key)
        _set_env_value("ASSEMBLYAI_API_KEY", key)
        return

    if settings.provider == PROVIDER_VULKAN:
        print("\nConfigurando runtime Whisper Large Vulkan compartilhado...")
        try:
            from helpers.vulkan_runtime import setup as setup_vulkan
        except ModuleNotFoundError:
            try:
                from vulkan_runtime import setup as setup_vulkan
            except ModuleNotFoundError:
                raise WizardError("Vulkan runtime helper not found")
        res = setup_vulkan()
        if res.get("status") != "pass":
            raise WizardError(f"Falha na configuração do runtime Vulkan: {res}")
        if settings.diarization != DIARIZATION_NONE:
            print("Baixando modelos ONNX do Pyannote para diarização DirectML...")
            try:
                from helpers.directml_diarization import ensure_diarization_models
            except ModuleNotFoundError:
                try:
                    from directml_diarization import ensure_diarization_models
                except ModuleNotFoundError:
                    raise WizardError("DirectML diarization helper not found")
            ensure_diarization_models()
        return

    raise WizardError(f"provider não suportado: {settings.provider}")


def doctor(settings: TranscriptionSettings) -> dict[str, object]:
    if settings.provider == PROVIDER_ELEVENLABS:
        return {
            "status": "pass" if _read_env_value("ELEVENLABS_API_KEY") else "unhealthy",
            "provider": settings.provider,
            "checks": {"elevenlabs_api_key": bool(_read_env_value("ELEVENLABS_API_KEY"))},
        }
    if settings.provider == PROVIDER_ASSEMBLYAI:
        return {
            "status": "pass" if _read_env_value("ASSEMBLYAI_API_KEY") else "unhealthy",
            "provider": settings.provider,
            "checks": {"assemblyai_api_key": bool(_read_env_value("ASSEMBLYAI_API_KEY"))},
        }
    if settings.provider == PROVIDER_VULKAN:
        try:
            from helpers.vulkan_runtime import doctor as doctor_vulkan
        except ModuleNotFoundError:
            try:
                from vulkan_runtime import doctor as doctor_vulkan
            except ModuleNotFoundError:
                return {"status": "unhealthy", "provider": settings.provider, "error": "helper missing"}
        doc = doctor_vulkan()
        if settings.diarization != DIARIZATION_NONE:
            try:
                from helpers.directml_diarization import doctor as doctor_dml
            except ModuleNotFoundError:
                try:
                    from directml_diarization import doctor as doctor_dml
                except ModuleNotFoundError:
                    doctor_dml = lambda: {"status": "unhealthy", "error": "helper missing"}
            dml_doc = doctor_dml()
            doc["checks"]["directml_diarization"] = dml_doc
            if dml_doc.get("status") != "pass":
                doc["status"] = "unhealthy"
        return doc

    return {
        "status": "unhealthy",
        "provider": settings.provider,
        "error": "unknown provider",
    }


def select_settings(
    *,
    default: TranscriptionSettings | None,
    provider: str | None,
    diarization: str | None,
    non_interactive: bool,
) -> TranscriptionSettings:
    if provider:
        selected_provider = provider
    else:
        if default:
            default_provider = default.provider
        else:
            default_provider = PROVIDER_VULKAN

        provider_map = [
            PROVIDER_VULKAN,
            PROVIDER_ASSEMBLYAI,
            PROVIDER_ELEVENLABS,
        ]
        default_index = provider_map.index(default_provider) if default_provider in provider_map else 0

        choice_idx = choose(
            "Qual provider de transcrição deseja utilizar?",
            [
                (
                    "Whisper Local (GPU Vulkan / DirectML) — Recomendado (NVIDIA / AMD / Intel)",
                    "Executa offline na GPU via Vulkan + DirectML com timestamps de palavras e Pyannote.",
                ),
                (
                    "AssemblyAI (Cloud)",
                    "Usa sua API Key e o modelo Best da AssemblyAI via nuvem.",
                ),
                (
                    "ElevenLabs Scribe (Cloud)",
                    "Usa sua API Key e o consumo da sua conta ElevenLabs.",
                ),
            ],
            default=default_index,
            non_interactive=non_interactive,
        )
        selected_provider = provider_map[choice_idx]

    if selected_provider == PROVIDER_ELEVENLABS:
        if diarization is not None:
            raise WizardError("--diarization só pode ser usado com o provider whisper-vulkan")
        return TranscriptionSettings.elevenlabs(
            language=default.language if default else "pt"
        )
    if selected_provider == PROVIDER_ASSEMBLYAI:
        if diarization is not None:
            raise WizardError("--diarization só pode ser usado com o provider whisper-vulkan")
        return TranscriptionSettings.assemblyai(
            language=default.language if default else "pt"
        )
    if selected_provider == PROVIDER_VULKAN:
        if diarization:
            selected_diarization = diarization
        else:
            default_diar_idx = (
                0
                if (default and default.provider == PROVIDER_VULKAN and default.diarization == DIARIZATION_COMMUNITY_1)
                else 0
            )
            diar_choice = choose(
                "Deseja habilitar Diarização Local (identificação de múltiplos locutores)?",
                [
                    (
                        "Diarização DirectML (Pyannote ONNX) — Recomendado para múltiplos locutores",
                        "Executa acelerado na GPU via DirectML com fallback automático em CPU.",
                    ),
                    (
                        "Sem Diarização (Mais rápido)",
                        "Ideal para vídeos de locutor único (aulas, tutoriais, talking-head).",
                    ),
                ],
                default=default_diar_idx,
                non_interactive=non_interactive,
            )
            selected_diarization = (
                DIARIZATION_COMMUNITY_1 if diar_choice == 0 else DIARIZATION_NONE
            )
        return TranscriptionSettings.vulkan(
            diarization=selected_diarization,
            language=default.language if default else "pt",
        )

    raise WizardError(f"provider não suportado: {selected_provider}")


def run_init(
    workspace: Path,
    *,
    provider: str | None = None,
    diarization: str | None = None,
    settings_output: Path | None = None,
    non_interactive: bool = False,
) -> TranscriptionSettings:
    workspace = workspace.expanduser().resolve()
    existing: TranscriptionSettings | None = None
    workspace_file = workspace_settings_path(workspace)
    if workspace_file.is_file():
        existing = read_settings(workspace_file)
    elif user_settings_path().is_file():
        existing = read_settings(user_settings_path())

    settings = select_settings(
        default=existing,
        provider=provider,
        diarization=diarization,
        non_interactive=non_interactive,
    )
    provision(settings, non_interactive=non_interactive)
    if settings_output:
        write_settings_atomic(settings_output, settings)
    else:
        workspace.mkdir(parents=True, exist_ok=True)
        write_settings_atomic(workspace_file, settings)
    if not user_settings_path().is_file():
        write_settings_atomic(user_settings_path(), settings)
    return settings


def run_configure(
    *,
    workspace: Path | None = None,
    provider: str | None = None,
    diarization: str | None = None,
    non_interactive: bool = False,
) -> TranscriptionSettings:
    existing: TranscriptionSettings | None = None
    target_file = workspace_settings_path(workspace) if workspace else user_settings_path()
    if target_file.is_file():
        existing = read_settings(target_file)
    elif user_settings_path().is_file():
        existing = read_settings(user_settings_path())

    settings = select_settings(
        default=existing,
        provider=provider,
        diarization=diarization,
        non_interactive=non_interactive,
    )
    provision(settings, non_interactive=non_interactive)
    write_settings_atomic(target_file, settings)
    print(f"Configuração salva em {target_file}")
    return settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alano Cut transcription setup wizard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_cmd = subparsers.add_parser("init", help="Initialize workspace settings")
    init_cmd.add_argument("workspace_pos", nargs="?", type=Path, default=None, help="Workspace directory")
    init_cmd.add_argument("--workspace", dest="workspace", type=Path, default=None, help="Workspace directory")
    init_cmd.add_argument("--settings-output", type=Path, default=None, help="Path to write settings JSON")
    init_cmd.add_argument("--provider", choices=[PROVIDER_VULKAN, PROVIDER_ASSEMBLYAI, PROVIDER_ELEVENLABS, "whisperx"])
    init_cmd.add_argument("--diarization", choices=[DIARIZATION_COMMUNITY_1, DIARIZATION_NONE])
    init_cmd.add_argument("--non-interactive", action="store_true")

    cfg_cmd = subparsers.add_parser("configure", help="Configure user or workspace settings")
    cfg_cmd.add_argument("--workspace", type=Path, help="Optional workspace directory")
    cfg_cmd.add_argument("--provider", choices=[PROVIDER_VULKAN, PROVIDER_ASSEMBLYAI, PROVIDER_ELEVENLABS, "whisperx"])
    cfg_cmd.add_argument("--diarization", choices=[DIARIZATION_COMMUNITY_1, DIARIZATION_NONE])
    cfg_cmd.add_argument("--non-interactive", action="store_true")

    doc_cmd = subparsers.add_parser("doctor", help="Run provider health checks")
    doc_cmd.add_argument("--workspace", type=Path, help="Optional workspace to inspect")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            target_ws = args.workspace or args.workspace_pos or Path.cwd()
            run_init(
                target_ws,
                provider=args.provider,
                diarization=args.diarization,
                settings_output=args.settings_output,
                non_interactive=args.non_interactive,
            )
            return 0
        if args.command == "configure":
            run_configure(
                workspace=args.workspace,
                provider=args.provider,
                diarization=args.diarization,
                non_interactive=args.non_interactive,
            )
            return 0
        if args.command == "doctor":
            current = (
                read_settings(workspace_settings_path(args.workspace))
                if args.workspace
                else read_settings(user_settings_path())
            )
            report = doctor(current)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report.get("status") == "pass" else 1
    except Exception as exc:
        print(f"erro na configuração: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
