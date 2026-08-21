"""Modular Prompts for the Agentic Editorial Loop Engine (Alano Rough Cut AI v0.6.0).

Defines specialized system prompts and task instructions for multi-turn cognitive editing:
- Phase 1: Global Diagnosis & Narrative Strategy (Strategy Agent)
- Phase 2: Timeline Sequence Assembly (Assembly Agent)
- Phase 3: Self-Critique & Autonomous Refinement (Supervisor / Reflection Agent)
"""

from __future__ import annotations

from helpers.knowledge_loader import compose_agent_knowledge


def get_agentic_system_prompt(video_type: str = "aula") -> str:
    """Build the compatible system prompt from durable knowledge and one archetype."""
    # The current engine still has a three-phase compatibility contract. The
    # manifest tasks/schemas are activated only with the four-phase tool
    # executor; injecting them here would contradict the JSON adapters below.
    durable_knowledge = compose_agent_knowledge(
        archetype=_infer_archetype(video_type)
    ).content
    compatibility_note = (
        "# Compatibilidade do EDL atual\n"
        "Use `is_list: true` somente quando o trecho for uma enumeração semântica real. "
        "A flag é apenas metadado editorial e não autoriza alterar os timestamps da evidência."
    )
    return f"{durable_knowledge}\n\n{compatibility_note}"


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
  "content_type": "videoaula | tutorial | reels | podcast | interview | talking_head | vsl | product_demo | testimonial | documentary | event_recap | custom",
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


def _infer_archetype(video_type: str) -> str | None:
    """Map the current CLI format vocabulary to one manifest archetype."""
    content_type = str(video_type or "").strip().lower()
    return {
        "aula": "educational_explainer",
        "videoaula": "educational_explainer",
        "educational": "educational_explainer",
        "explainer": "educational_explainer",
        "reels": "social_talking_head",
        "short": "social_talking_head",
        "social": "social_talking_head",
        "talking_head": "social_talking_head",
        "tutorial": "tutorial",
        "vsl": "sales_vsl",
        "sales_vsl": "sales_vsl",
        "interview": "interview",
        "podcast": "podcast_excerpt",
        "product_demo": "product_demo",
        "testimonial": "testimonial_case_study",
        "documentary": "documentary_narrative",
        "event_recap": "event_recap",
    }.get(content_type)
