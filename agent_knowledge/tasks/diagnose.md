# Tarefa: diagnosticar o material

Objetivo: compreender o projeto inteiro antes de decidir a montagem.

## Entradas

- `brief.md`, quando fornecido;
- `transcripts/<source>.json` de cada fonte;
- módulos `always` do manifesto.

## Procedimento

1. Leia todas as fontes e identifique objetivo, audiência/uso e promessa central.
2. Classifique o tipo de conteúdo e selecione o arquétipo mais próximo. Quando o tipo explícito for `custom` ou nenhum módulo representar a estrutura real, registre `selected_archetype: null`; `custom` é tipo de conteúdo, nunca nome de módulo.
3. Agrupe retakes por função narrativa e compare correção, completude, clareza e continuidade.
4. Liste fala de produção, falsos inícios, erros, duplicações e tangentes com evidência.
5. Identifique must-keep, must-avoid, beats candidatos e conflitos com o target de duração.
6. Registre hipóteses e incertezas. Só use `needs_human_review` quando a falta de informação puder mudar materialmente o corte.

Não selecione a timeline final e não grave uma EDL nesta fase.

## Saída

Grave `diagnosis.json` via `write_artifact` com todos os campos obrigatórios:

```json
{
  "schema_version": 1,
  "artifact": "diagnosis",
  "revision": 1,
  "status": "ready",
  "content_type": "videoaula",
  "selected_archetype": "educational_explainer",
  "confidence": 0.9,
  "narrative_objective": "Apresentar a plataforma Ticto e os seus 3 perfis de usuário.",
  "audience": "Produtores, afiliados e empreendedores digitais.",
  "platform_or_use": "Treinamento / Videoaula",
  "recording_style": "multi_take",
  "runtime_target": {
    "source": "inferred",
    "min_seconds": 60,
    "max_seconds": 120,
    "rationale": "Ritmo dinâmico para videoaula introdutória."
  },
  "assumptions": ["Audio principal nas fontes gravadas."],
  "uncertainties": [],
  "retake_families": [],
  "production_speech": [],
  "must_keep": [],
  "must_avoid": [
    {
      "evidence": {
        "source": "SRC_66F16BBAFBCC0127",
        "start": 118.96,
        "end": 122.50,
        "quote": "Beleza."
      },
      "reason": "Caco de bastidor isolado antes do início da fala real."
    }
  ],
  "candidate_beats": [
    {
      "id": "BEAT_1",
      "purpose": "Abertura e introdução.",
      "required": true,
      "evidence": [
        {
          "source": "SRC_66F16BBAFBCC0127",
          "start": 127.58,
          "end": 133.00,
          "quote": "Olá, sejam muito bem-vindos ao treinamento completo da Ticto."
        }
      ]
    }
  ]
}
```

IMPORTANTE: Use sempre os IDs de fonte reais de `edl_template.json` e os timestamps `start`/`end` exatos das palavras lidas em `transcripts/<source>.json`. Nunca corte no meio de uma palavra. Mantenha o diagnóstico executivo e conciso, emitindo `write_artifact` diretamente sem rodeios.
