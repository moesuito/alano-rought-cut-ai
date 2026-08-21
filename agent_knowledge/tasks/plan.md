# Tarefa: construir o plano de corte

Objetivo: transformar o diagnóstico em beats narrativos verificáveis antes de escolher todos os ranges finais.

## Entradas

- última revisão válida de `diagnosis.json`;
- `brief.md`, quando fornecido;
- `transcripts/<source>.json` de cada fonte;
- o único arquétipo indicado por `selected_archetype`, quando esse campo não for nulo; diagnósticos `custom` usam somente o core.

## Procedimento

1. Adapte o arquétipo ao conteúdo quando houver um; com `selected_archetype: null`, derive a estrutura do diagnóstico sem inventar um módulo de formato.
2. Ordene beats pela lógica da audiência, não pela gravação.
3. Para cada beat, declare função, obrigatoriedade, critérios de seleção e candidatos sustentados por evidência.
4. Resolva exclusões e retakes no nível do plano. Preserve alternativas somente quando a escolha depender de evidência ainda não lida.
5. Explique o pacing em termos editoriais e estime duração sem fabricar precisão.
6. Verifique se hook/contexto/desenvolvimento/payoff/encerramento formam uma progressão adequada ao formato.

Não produza ranges definitivos nesta fase. Um candidato pode usar o range compacto de uma frase.

## Saída

Grave `cut_plan.json` via `write_artifact` com todos os campos obrigatórios:

```json
{
  "schema_version": 1,
  "artifact": "cut_plan",
  "revision": 1,
  "diagnosis_revision": 1,
  "status": "ready",
  "archetype": "educational_explainer",
  "narrative_objective": "Apresentar a plataforma Ticto e seus 3 perfis de usuário.",
  "pacing_strategy": "Cortes diretos e transições limpas mantendo o fluxo didático.",
  "target_duration_seconds": 90.0,
  "beats": [
    {
      "id": "BEAT_1",
      "position": 1,
      "purpose": "Abertura e introdução.",
      "required": true,
      "selection_requirements": ["Clareza e identificação"],
      "candidates": [
        {
          "source": "SRC_66F16BBAFBCC0127",
          "start": 127.58,
          "end": 133.00,
          "quote": "Olá, sejam muito bem-vindos ao treinamento completo da Ticto.",
          "selection_note": "Abertura oficial do vídeo com energia e clareza."
        }
      ],
      "transition_intent": "hard_cut"
    }
  ],
  "global_exclusions": [],
  "open_questions": []
}
```

Preencha os beats com IDs únicos e posições contíguas (1, 2, 3...). Obtenha os timestamps `start` e `end` lendo as palavras em `transcripts/<source>.json`. Emita a chamada `write_artifact` diretamente.
