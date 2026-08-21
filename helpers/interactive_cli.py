"""Interactive Terminal User Interface (TUI) for Alano Rough Cut AI.

Provides a polished, modern terminal experience with zero folder pollution.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure standard streams use UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.status import Status
from rich.table import Table
from rich.text import Text

from helpers.llm_client import load_env_file
from helpers.orchestrator import run_autonomous_rough_cut, scan_inventory


console = Console()


VIDEO_TYPE_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("1", "videoaula", "Videoaula / conteúdo educacional"),
    ("2", "tutorial", "Tutorial"),
    ("3", "reels", "Reels / Shorts / social"),
    ("4", "podcast", "Podcast"),
    ("5", "interview", "Entrevista"),
    ("6", "talking_head", "Talking head / YouTube"),
    ("7", "vsl", "VSL"),
    ("8", "product_demo", "Demonstração de produto"),
    ("9", "testimonial", "Depoimento / estudo de caso"),
    ("10", "documentary", "Documentário narrativo"),
    ("11", "event_recap", "Resumo de evento"),
    ("12", "custom", "Outro / personalizado"),
)


def resolve_working_directory() -> Path:
    """Return the exact directory where the argument-free CLI was invoked."""
    return Path.cwd().resolve()


def format_seconds(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def display_header() -> None:
    """Render modern gradient ASCII header."""
    header_text = Text()
    header_text.append("🎬 ALANO ROUGH CUT AI ", style="bold cyan")
    header_text.append("v0.6.0\n", style="bold yellow")
    header_text.append("Autonomous Agentic Rough Cut Engine — Local GPU + Multi-Turn Cognitive LLM", style="dim white")

    panel = Panel(
        Align.center(header_text),
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 2),
    )
    console.print(panel)


def display_media_table(inventory: list[dict[str, Any]], working_dir: Path) -> None:
    """Render sleek table with discovered media files."""
    table = Table(
        title=f"📁 Vídeos Encontrados em [bold white]{working_dir.name}/[/bold white]",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        border_style="dim",
        expand=True,
    )

    table.add_column("#", justify="center", style="dim", width=4)
    table.add_column("Arquivo", style="bold white", ratio=4)
    table.add_column("Duração", justify="right", style="green", ratio=2)
    table.add_column("Resolução", justify="center", style="yellow", ratio=2)
    table.add_column("FPS", justify="center", style="magenta", ratio=2)

    total_dur = 0.0
    for i, item in enumerate(inventory, 1):
        meta = item["meta"]
        dur = meta["duration"]
        total_dur += dur
        res = f"{meta['width']}x{meta['height']}"
        fps = meta["fps"]
        if fps == "30000/1001":
            fps = "29.97"
        elif fps == "24000/1001":
            fps = "23.976"

        table.add_row(
            str(i),
            item["filename"],
            format_seconds(dur),
            res,
            fps,
        )

    console.print(table)
    console.print(
        f"[dim]Total:[/dim] [bold white]{len(inventory)} arquivos[/bold white] "
        f"([cyan]{format_seconds(total_dur)}[/cyan] de gravação bruta)\n"
    )


def interactive_main() -> None:
    """Main interactive terminal flow."""
    load_env_file()
    console.clear()
    display_header()

    working_dir = resolve_working_directory()

    # 1. Scan for raw videos in current working directory
    try:
        inventory = scan_inventory(working_dir)
    except ValueError:
        inventory = []

    if not inventory:
        console.print(
            Panel(
                f"[bold red]Nenhum arquivo de vídeo encontrado na pasta atual:[/bold red]\n"
                f"[yellow]{working_dir}[/yellow]\n\n"
                f"[white]Coloque seus arquivos gravados (.mov, .mp4, .mkv) nesta pasta e execute [bold cyan]alanocut[/bold cyan] novamente.[/white]",
                title="⚠️ Mídia não encontrada",
                border_style="red",
                box=box.ROUNDED,
            )
        )
        sys.exit(1)

    display_media_table(inventory, working_dir)

    # 2. Interactive Selection: Video Type
    console.print(Rule(title="⚙️ Configuração do Corte", style="dim"))
    console.print("[bold white]Selecione o formato do vídeo:[/bold white]")
    for key, _value, label in VIDEO_TYPE_OPTIONS:
        console.print(f"  [bold cyan]{key:>2}[/bold cyan] [bold]{label}[/bold]")

    type_choice = Prompt.ask(
        "\n> Escolha o formato",
        choices=[key for key, _value, _label in VIDEO_TYPE_OPTIONS],
        default="1",
        show_choices=False,
    )

    type_mapping = {key: value for key, value, _label in VIDEO_TYPE_OPTIONS}
    video_type = type_mapping[type_choice]

    # 3. Optional Briefing
    console.print("\n[bold white]Briefing ou instruções específicas[/bold white] [dim](Opcional - Pressione Enter para modo 100% automático):[/dim]")
    brief = Prompt.ask(">", default="", show_default=False).strip()

    # 4. Confirmation Summary Card
    console.print("")
    console.print(Rule(title="📋 Resumo da Operação", style="cyan"))
    summary_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    summary_table.add_column("Chave", style="dim cyan", width=22)
    summary_table.add_column("Valor", style="bold white")

    summary_table.add_row("Pasta do Projeto:", str(working_dir))
    summary_table.add_row("Arquivos Brutos:", f"{len(inventory)} vídeos")
    selected_label = next(
        label for key, _value, label in VIDEO_TYPE_OPTIONS if key == type_choice
    )
    summary_table.add_row("Formato Escolhido:", selected_label)
    summary_table.add_row("Motor Editorial:", "[bold cyan]Executor por artefatos (multi-turno)[/bold cyan]")
    summary_table.add_row("Briefing Editorial:", brief if brief else "[italic green]Automático (A IA decidirá os melhores takes)[/italic green]")
    summary_table.add_row("Arquivo de Entrega:", "[bold yellow]./timeline.xml[/bold yellow] (Premiere Pro)")

    console.print(Panel(summary_table, border_style="cyan", box=box.ROUNDED))

    confirmed = Confirm.ask("\n[bold green]Confirmar e iniciar montagem autônoma?[/bold green]", default=True)
    if not confirmed:
        console.print("[yellow]Operação cancelada pelo usuário.[/yellow]")
        sys.exit(0)

    # 5. Live Autonomous Execution with Rich Status Spinner
    console.print("")
    with Status("[bold cyan]Iniciando motor autônomo AlanoCut...[/bold cyan]", spinner="dots", console=console) as status:
        def on_progress(step: str, detail: str) -> None:
            status.update(f"[bold cyan][{step}][/bold cyan] [white]{detail}[/white]")

        try:
            res = run_autonomous_rough_cut(
                raw_dir=working_dir,
                brief=brief,
                video_type=video_type,
                progress_callback=on_progress,
            )
        except Exception as e:
            console.print(f"\n[bold red]❌ Erro durante a execução:[/bold red] {e}")
            sys.exit(1)

    if res.get("status") != "success":
        console.clear()
        display_header()
        review_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        review_table.add_column("Métrica", style="yellow", width=25)
        review_table.add_column("Resultado", style="bold white")
        review_table.add_row("Status:", "[bold yellow]REVISÃO HUMANA NECESSÁRIA[/bold yellow]")
        review_table.add_row("Código:", str(res.get("error_code", "REVIEW_REQUIRED")))
        review_table.add_row("Estado do agente:", str(res.get("agent_state", "unknown")))
        review_table.add_row("Estado do QC:", str(res.get("qc_status", "pending")))
        review_table.add_row("Timeline publicada:", "[bold]não[/bold]")
        review_table.add_row("Pasta da sessão:", f"[dim]{res.get('session_dir', '')}[/dim]")
        review_table.add_row("Log estruturado:", f"[dim cyan]{res.get('session_log', '')}[/dim cyan]")
        console.print(
            Panel(
                review_table,
                title="⏸️ Pipeline pausado com segurança",
                border_style="yellow",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
        console.print(
            "\n[white]Nenhum XML foi publicado. Consulte os artefatos e gates da sessão antes de retomar.[/white]"
        )
        raise SystemExit(2)

    # 6. Final Success Panel
    console.clear()
    display_header()

    xml_path = res["timeline_xml"]
    takes_count = res["takes_count"]
    dur_s = res["total_duration_s"]

    success_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    success_table.add_column("Métrica", style="cyan", width=25)
    success_table.add_column("Resultado", style="bold white")

    success_table.add_row("🎉 Status:", "[bold green]CONCLUÍDO COM SUCESSO[/bold green]")
    success_table.add_row("🎞️ Timeline Final:", f"[bold yellow]{xml_path}[/bold yellow]")
    success_table.add_row("✂️ Total de Cortes:", f"[bold white]{takes_count} takes selecionados[/bold white]")
    success_table.add_row("⏱️ Duração do Rough Cut:", f"[bold green]{format_seconds(dur_s)}[/bold green] ({dur_s:.1f}s)")
    success_table.add_row("🤖 Estado do Agente:", f"[bold green]{res.get('agent_state', 'approved').upper()}[/bold green]")
    success_table.add_row("🔊 Readiness / QC:", f"[bold green]{res.get('qc_status', 'pass').upper()}[/bold green]")
    success_table.add_row("📝 Relatório Editorial (.txt):", f"[bold cyan]{res.get('audit_txt', '')}[/bold cyan]")
    success_table.add_row("📜 Log Completo (.log):", f"[dim cyan]{res.get('session_log', '')}[/dim cyan]")
    success_table.add_row("📁 Pasta da Sessão:", f"[dim]{res['session_dir']}[/dim]")

    console.print(
        Panel(
            success_table,
            title="✨ Montagem Finalizada",
            border_style="green",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print("\n[bold white]👉 Próximo passo:[/bold white] Abra o [cyan]Adobe Premiere Pro[/cyan], vá em [bold]Arquivo > Importar[/bold] e selecione o [yellow]timeline.xml[/yellow]!")


if __name__ == "__main__":
    interactive_main()
