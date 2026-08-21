"""Modular Prompts for the Agentic Editorial Loop Engine (Alano Rough Cut AI v0.6.0).

Defines specialized system prompts and task instructions for multi-turn cognitive editing:
- Phase 1: Global Diagnosis & Narrative Strategy (Strategy Agent)
- Phase 2: Timeline Sequence Assembly (Assembly Agent)
- Phase 3: Self-Critique & Autonomous Refinement (Supervisor / Reflection Agent)
"""

from __future__ import annotations

import os
from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parent


def _load_agentic_system_prompt_template() -> str:
    """Load the agentic editor persona and durable knowledge from Markdown."""
    prompt_file = PROMPTS_DIR / "agentic_editor_system_prompt.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")

    appdata_prompt = (
        Path(os.environ.get("APPDATA", ""))
        / "alano-rought-cut-ai"
        / "helpers"
        / "prompts"
        / "agentic_editor_system_prompt.md"
    )
    if appdata_prompt.exists():
        return appdata_prompt.read_text(encoding="utf-8")

    raise FileNotFoundError(
        "agentic_editor_system_prompt.md not found at "
        f"{prompt_file} or {appdata_prompt}"
    )


def get_agentic_system_prompt(video_type: str = "aula") -> str:
    """Build the agentic system prompt from Markdown plus format-specific rules."""
    is_short = video_type.lower() in {"reels", "tiktok", "shorts", "social", "short"}

    if is_short:
        pacing_rules = (
            "- FORMATO: VÍDEO CURTO / REDES SOCIAIS (Reels / TikTok / YouTube Shorts).\n"
            "- OBJETIVO: Ritmo acelerado (fast-pacing), gancho forte nos primeiros 3 segundos, retenção máxima.\n"
            "- DURAÇÃO MÁXIMA: Estritamente <= 90 segundos (ideal 30s a 60s).\n"
            "- LISTAS: Não aplicar preservação de lista ('is_list': false); cortes rápidos e diretos."
        )
    else:
        pacing_rules = (
            "- FORMATO: VÍDEO LONGO / EDUCACIONAL (Videoaula / Tutorial / Curso / YouTube).\n"
            "- OBJETIVO: Didático, fluido, natural, cadenciado e completo.\n"
            "- LISTAS E ENUMERAÇÕES: Quando o apresentador listar recursos ou passos em sequência, "
            "marque 'is_list': true para preservar as micropausas naturais de respiração (até 500ms)."
        )

    template = _load_agentic_system_prompt_template()
    return template.replace("{pacing_rules}", pacing_rules)


def build_phase1_strategy_prompt(brief: str, takes_packed_content: str) -> str:
    """Prompt for Phase 1: Global Diagnosis and Editorial Strategy."""
    return f"""════════════════════════════════════════════════════════════════════════════════
TAREFA 1: DIAGNÓSTICO GLOBAL & ESTRATÉGIA NARRATIVA
════════════════════════════════════════════════════════════════════════════════

BRIEFING DO USUÁRIO:
{brief if brief else "(Nenhum briefing fornecido — atue com 100% de autonomia sênior)"}

TRANSCRIÇÕES COMPLETAS DO MATERIAL BRUTO:
{takes_packed_content}

INSTRUÇÃO DA TAREFA 1:
Analise a totalidade do material acima e produza o Diagnóstico e Plano Estratégico do vídeo no seguinte formato JSON estrito:

```json
{{
  "content_type": "aula | reels | tutorial | vsl | talking_head",
  "narrative_objective": "Objetivo central do vídeo em 1 frase",
  "recording_style": "linear | fragmented_pickups | multi_take",
  "retake_resolutions": [
    {{
      "topic": "Tema ou frase repetida",
      "rejected_takes": ["Detalhes dos takes descartados e o porquê (erros, cacos, hesitações)"],
      "winning_take": "Take escolhido e justificativa"
    }}
  ],
  "elimination_list": [
    "Lista de cacos de bastidor, falsos inícios e ruídos que serão 100% eliminados"
  ],
  "narrative_blocks": [
    {{
      "beat": "HOOK_INTRO | DEFINICAO | PROPOSTA_VALOR | PONTOS_CHAVE | CTA_ENCERRAMENTO",
      "source": "ID_DO_ARQUIVO",
      "content_summary": "Resumo do que será dito neste bloco",
      "is_list": false
    }}
  ]
}}
```

Retorne APENAS o JSON da estratégia."""


def build_phase2_assembly_prompt(strategy_json_str: str) -> str:
    """Prompt for Phase 2: Decupage & Concrete EDL Generation."""
    return f"""════════════════════════════════════════════════════════════════════════════════
TAREFA 2: DECUPAGEM DA LINHA DO TEMPO (EDL PRELIMINAR)
════════════════════════════════════════════════════════════════════════════════

ESTRATÉGIA APROVADA NA TAREFA 1:
{strategy_json_str}

INSTRUÇÃO DA TAREFA 2:
Com base na estratégia planejada, extraia os blocos de corte exatos das transcrições com timestamps reais de palavra/frase.

Retorne APENAS um array JSON de objetos ordenados na sequência final da montagem:
```json
[
  {{
    "source": "ID_DO_ARQUIVO_FONTE",
    "start": 28.02,
    "end": 42.62,
    "beat": "HOOK_INTRO",
    "quote": "Citação literal do trecho selecionado",
    "reason": "Justificativa editorial concisa",
    "is_list": false
  }}
]
```

Retorne APENAS o array JSON da EDL preliminar."""


def build_phase3_reflection_prompt(current_edl_json_str: str, iteration: int) -> str:
    """Prompt for Phase 3: Self-Critique and Autonomous Refinement Loop."""
    return f"""════════════════════════════════════════════════════════════════════════════════
TAREFA 3: AUDITORIA CRÍTICA & AUTO-REFLEXÃO EDITORIAL (Loop #{iteration})
════════════════════════════════════════════════════════════════════════════════

EDL ATUAL EM INSPEÇÃO:
{current_edl_json_str}

INSTRUÇÃO DE AUDITORIA:
Você é o Supervisor de Qualidade e Chefe de Montagem inspecionando a EDL gerada. Realize uma inspeção clínica:
1. Verifique se algum corte inicia ou termina com cacos de bastidor (ex: 'Beleza.', 'Tá.', 'Corta', 'Volta', 'Aí', pigarros).
2. Verifique se a ordem dos cortes possui continuidade narrativa e gramatical fluida.
3. Verifique se algum falso início ou take descartado na estratégia passou inadvertidamente.
4. Verifique se a duração total e o ritmo respeitam o tipo de vídeo.

DECISÃO DO AGENTE:
- Se encontrar QUALQUER defeito, ajuste os ranges e retorne a versão corrigida da EDL com `"status": "REFINED"`.
- Se o corte estiver 100% aprovado, sem defeitos e pronto para entrega, retorne `"status": "APPROVED"`.

Formato de resposta JSON obrigatório:
```json
{{
  "status": "APPROVED | REFINED",
  "critique_notes": [
    "Pontos observados durante a auto-inspeção"
  ],
  "refined_ranges": [
    {{
      "source": "ID_DO_ARQUIVO_FONTE",
      "start": 28.02,
      "end": 42.62,
      "beat": "HOOK_INTRO",
      "quote": "Citação literal do trecho",
      "reason": "Justificativa da decisão",
      "is_list": false
    }}
  ]
}}
```

Retorne APENAS o JSON de decisão."""
