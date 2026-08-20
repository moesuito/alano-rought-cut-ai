"""Interactive, reusable setup wizard for Alano Cut transcription profiles.

PowerShell owns bootstrap/update mechanics.  This module owns all provider
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
        PROVIDER_WHISPERX,
        SettingsError,
        TranscriptionSettings,
        global_env_path,
        read_settings,
        user_settings_path,
        workspace_settings_path,
        write_settings_atomic,
    )
    from helpers.whisperx_runtime import RuntimeContractError, locate_runtime_python
except ModuleNotFoundError as exc:
    if exc.name != "helpers":
        raise
    from transcription_settings import (  # type: ignore[no-redef]
        DIARIZATION_COMMUNITY_1,
        DIARIZATION_NONE,
        PROVIDER_ASSEMBLYAI,
        PROVIDER_ELEVENLABS,
        PROVIDER_WHISPERX,
        SettingsError,
        TranscriptionSettings,
        global_env_path,
        read_settings,
        user_settings_path,
        workspace_settings_path,
        write_settings_atomic,
    )
    from whisperx_runtime import RuntimeContractError, locate_runtime_python  # type: ignore[no-redef]


MODEL_PAGE = "https://huggingface.co/pyannote/speaker-diarization-community-1"
TOKEN_PAGE = "https://huggingface.co/settings/tokens"
HF_CONFIG_URL = (
    "https://huggingface.co/pyannote/speaker-diarization-community-1/resolve/"
    "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee/config.yaml"
)
MIN_FREE_GIB = 12
RECOMMENDED_FREE_GIB = 18


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
    updated: list[str] = []
    found = False
    for line in existing:
        if "=" in line and line.split("=", 1)[0].strip() == name:
            if not found:
                updated.append(f"{name}={value}")
                found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{name}={value}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write("\n".join(updated) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _print_choices(title: str, options: list[tuple[str, str]], selected: int) -> None:
    print()
    print(title)
    for index, (label, detail) in enumerate(options):
        marker = "[x]" if index == selected else "[ ]"
        print(f"  {marker} {label}")
        print(f"    {detail}")


def choose(
    title: str,
    options: list[tuple[str, str]],
    *,
    default: int = 0,
    non_interactive: bool = False,
    input_fn: Callable[[str], str] = input,
) -> int:
    """Render a radio-style menu with deterministic numeric fallback."""

    if non_interactive:
        return default
    _print_choices(title, options, default)
    while True:
        answer = input_fn(f"Escolha [1-{len(options)}] (Enter = {default + 1}): ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("Escolha inválida. Informe o número de uma opção.")


def confirm(prompt: str, *, default: bool, non_interactive: bool) -> bool:
    if non_interactive:
        return default
    marker = "S/n" if default else "s/N"
    while True:
        answer = input(f"{prompt} [{marker}]: ").strip().casefold()
        if not answer:
            return default
        if answer in {"s", "sim", "y", "yes"}:
            return True
        if answer in {"n", "nao", "não", "no"}:
            return False
        print("Responda sim ou não.")


def validate_hf_token(token: str) -> None:
    request = urllib.request.Request(HF_CONFIG_URL, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise WizardError("o token não possui acesso ao Community-1")
    except urllib.error.HTTPError as exc:
        raise WizardError(
            "não foi possível acessar o Community-1; aceite o gate e use um token Read/fine-grained válido"
        ) from exc
    except urllib.error.URLError as exc:
        raise WizardError(f"não foi possível validar o Hugging Face: {exc.reason}") from exc


def validate_elevenlabs_key(key: str) -> None:
    request = urllib.request.Request(
        "https://api.elevenlabs.io/v1/user",
        headers={"xi-api-key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise WizardError("a API key da ElevenLabs foi recusada")
    except urllib.error.HTTPError as exc:
        raise WizardError("a API key da ElevenLabs foi recusada") from exc
    except urllib.error.URLError as exc:
        raise WizardError(f"não foi possível validar a ElevenLabs: {exc.reason}") from exc


def validate_assemblyai_key(key: str) -> None:
    request = urllib.request.Request(
        "https://api.assemblyai.com/v2/transcript?limit=1",
        headers={"authorization": key, "User-Agent": "AlanoCut/0.4.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in {200, 201}:
                raise WizardError("a API key da AssemblyAI foi recusada")
    except urllib.error.HTTPError as exc:
        raise WizardError("a API key da AssemblyAI foi recusada") from exc
    except urllib.error.URLError as exc:
        raise WizardError(f"não foi possível validar a AssemblyAI: {exc.reason}") from exc


def _check_space(*, non_interactive: bool) -> None:
    target = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    free_gib = shutil.disk_usage(target).free / (1024**3)
    if free_gib < MIN_FREE_GIB:
        raise WizardError(
            f"espaço insuficiente em {target.drive or target}: {free_gib:.1f} GiB livres; "
            f"são necessários pelo menos {MIN_FREE_GIB} GiB"
        )
    if free_gib < RECOMMENDED_FREE_GIB:
        if not confirm(
            f"Há {free_gib:.1f} GiB livres. O perfil local recomenda {RECOMMENDED_FREE_GIB} GiB. Continuar?",
            default=False,
            non_interactive=non_interactive,
        ):
            raise WizardError("configuração cancelada por falta de espaço recomendado")


def _helper_path(name: str) -> Path:
    helper = Path(__file__).resolve().with_name(name)
    if not helper.is_file():
        raise WizardError(f"helper ausente da instalação: {helper}")
    return helper


def _run_helper(arguments: list[str], *, python: str | Path | None = None) -> None:
    completed = subprocess.run([str(python or sys.executable), *arguments], check=False)
    if completed.returncode != 0:
        raise WizardError("a configuração do runtime/modelos falhou; veja o diagnóstico acima")


def _runtime_python() -> Path:
    """Find the heavy shared interpreter only after the bootstrap has completed."""

    try:
        return locate_runtime_python()
    except RuntimeContractError as exc:
        raise WizardError(f"runtime CUDA indisponível: {exc}") from exc


def provision(settings: TranscriptionSettings, *, non_interactive: bool) -> None:
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

    print(
        "O perfil WhisperX local reserva aproximadamente 16 GiB entre runtime e modelos "
        "(18 GiB livres recomendados). Ele opera somente em CUDA; não há fallback em CPU."
    )
    _check_space(non_interactive=non_interactive)
    if settings.diarization == DIARIZATION_COMMUNITY_1:
        print()
        print("Para usar o Pyannote Community-1:")
        print(f"  1. Abra {MODEL_PAGE} e aceite as condições do modelo.")
        print(f"  2. Crie um token Read ou fine-grained em {TOKEN_PAGE}.")
        print("  3. Cole um token cuja conta já tenha acesso ao gate.")
        token = _read_env_value("HF_TOKEN") or _read_env_value("HUGGING_FACE_HUB_TOKEN")
        if not token:
            if non_interactive:
                raise WizardError("HF_TOKEN é obrigatório para Community-1 no modo não interativo")
            token = getpass.getpass("Cole o Hugging Face access token (entrada oculta): ").strip()
        if not token:
            raise WizardError("nenhum token Hugging Face foi informado")
        if not os.environ.get("ALANOCUT_SKIP_CREDENTIAL_VALIDATION"):
            validate_hf_token(token)
        _set_env_value("HF_TOKEN", token)
        os.environ["HF_TOKEN"] = token

    print("\nConfigurando runtime CUDA compartilhado. Isso pode levar alguns minutos...")
    _run_helper([str(_helper_path("whisperx_runtime.py")), "setup"])
    runtime_python = _runtime_python()
    profile = settings.diarization
    print("Baixando os modelos selecionados para o cache compartilhado...")
    _run_helper(
        [str(_helper_path("transcription_models.py")), "prefetch", "--profile", profile],
        python=runtime_python,
    )


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
    runtime = _helper_path("whisperx_runtime.py")
    models = _helper_path("transcription_models.py")
    runtime_result = subprocess.run([sys.executable, str(runtime), "doctor"], check=False)
    try:
        runtime_python = _runtime_python()
    except WizardError:
        runtime_python = None
    model_result = subprocess.run(
        [str(runtime_python or sys.executable), str(models), "doctor", "--profile", settings.diarization],
        check=False,
    )
    return {
        "status": "pass" if runtime_result.returncode == 0 and model_result.returncode == 0 else "unhealthy",
        "provider": settings.provider,
        "diarization": settings.diarization,
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
        default_provider = default.provider if default else PROVIDER_WHISPERX
        default_index = 0 if default_provider == PROVIDER_WHISPERX else (1 if default_provider == PROVIDER_ELEVENLABS else 2)
        choice_idx = choose(
            "Qual provider de transcrição deseja utilizar?",
            [
                (
                    "WhisperX local — Recomendado",
                    "Executa em NVIDIA CUDA; não consome API externa.",
                ),
                (
                    "ElevenLabs Scribe",
                    "Usa sua API Key e o consumo da sua conta ElevenLabs.",
                ),
                (
                    "AssemblyAI (Cloud)",
                    "Usa sua API Key e o modelo Best da AssemblyAI.",
                ),
            ],
            default=default_index,
            non_interactive=non_interactive,
        )
        if choice_idx == 0:
            selected_provider = PROVIDER_WHISPERX
        elif choice_idx == 1:
            selected_provider = PROVIDER_ELEVENLABS
        else:
            selected_provider = PROVIDER_ASSEMBLYAI
    if selected_provider == PROVIDER_ELEVENLABS:
        if diarization is not None:
            raise WizardError("--diarization só pode ser usado com o provider whisperx")
        return TranscriptionSettings.elevenlabs(
            language=default.language if default else "pt"
        )
    if selected_provider == PROVIDER_ASSEMBLYAI:
        if diarization is not None:
            raise WizardError("--diarization só pode ser usado com o provider whisperx")
        return TranscriptionSettings.assemblyai(
            language=default.language if default else "pt"
        )
    if selected_provider != PROVIDER_WHISPERX:
        raise WizardError(f"provider não suportado: {selected_provider}")

    if diarization:
        selected_diarization = diarization
    else:
        default_diarization = (
            default.diarization
            if default and default.provider == PROVIDER_WHISPERX
            else DIARIZATION_COMMUNITY_1
        )
        selected_diarization = (
            DIARIZATION_COMMUNITY_1
            if choose(
                "Deseja separar os speakers?",
                [
                    (
                        "Pyannote Community-1 — Recomendado",
                        "Separa speakers; exige gate e token Hugging Face.",
                    ),
                    (
                        "Sem diarização",
                        "Mantém WhisperX/alinhamento, mas sem identificação de speakers.",
                    ),
                ],
                default=0 if default_diarization == DIARIZATION_COMMUNITY_1 else 1,
                non_interactive=non_interactive,
            )
            == 0
            else DIARIZATION_NONE
        )
    return TranscriptionSettings.whisperx(
        diarization=selected_diarization,
        language=default.language if default else "pt",
    )


def _existing_default(workspace: Path | None) -> TranscriptionSettings | None:
    candidates = [workspace_settings_path(workspace)] if workspace else []
    candidates.append(user_settings_path())
    for path in candidates:
        if path.is_file():
            try:
                return read_settings(path)
            except SettingsError:
                continue
    return None


def run_wizard(args: argparse.Namespace) -> Path:
    workspace = Path(args.workspace).resolve() if args.workspace else None
    default = _existing_default(workspace)
    non_interactive = bool(args.non_interactive)
    if non_interactive and not args.provider and default is None:
        raise WizardError("--provider é obrigatório em modo não interativo sem configuração prévia")
    reuse_existing = (
        args.command in {"install", "setup"}
        and default is not None
        and args.provider is None
        and args.diarization is None
    )
    settings = default if reuse_existing else select_settings(
        default=default,
        provider=args.provider,
        diarization=args.diarization,
        non_interactive=non_interactive,
    )
    if settings is None:
        raise WizardError("nenhum provider foi selecionado")
    provision(settings, non_interactive=non_interactive)
    if args.settings_output:
        destination = Path(args.settings_output).resolve()
    elif workspace:
        destination = workspace_settings_path(workspace)
    else:
        destination = user_settings_path()
    return write_settings_atomic(destination, settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Configure Alano Cut transcription")
    parser.add_argument("command", choices=("install", "configure", "init", "setup", "doctor", "migrate"))
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--settings-output", type=Path, default=None)
    parser.add_argument("--provider", choices=(PROVIDER_WHISPERX, PROVIDER_ELEVENLABS, PROVIDER_ASSEMBLYAI))
    parser.add_argument("--diarization", choices=(DIARIZATION_COMMUNITY_1, DIARIZATION_NONE))
    parser.add_argument("--non-interactive", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            default = _existing_default(Path(args.workspace).resolve() if args.workspace else None)
            if default is None:
                raise WizardError("nenhum provider configurado; execute `alanocut configure`")
            result = doctor(default)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result.get("status") == "pass" else 2
        if args.command == "migrate" and user_settings_path().is_file():
            default = _existing_default(None)
            if default is not None:
                result = doctor(default)
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
                if result.get("status") == "pass":
                    return 0
                print("A configuração existente precisa ser reparada; abrindo o setup guiado.")
        destination = run_wizard(args)
        print(f"Configuração salva em {destination}")
        return 0
    except (WizardError, SettingsError) as exc:
        print(f"Setup falhou: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
