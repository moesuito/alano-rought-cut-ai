# Tarefa: revisar e decidir

Objetivo: criticar a EDL contra brief, diagnóstico, plano e evidência — não apenas contra sua própria forma JSON.

## Checklist obrigatório

1. A promessa e o objetivo do brief foram cumpridos?
2. Beats obrigatórios existem e estão na ordem defensável?
3. Algum erro, falso início, bastidor ou retake rejeitado sobreviveu?
4. Há repetição sem função ou ausência de contexto necessário?
5. Toda junção preserva continuidade semântica e gramatical?
6. Fontes, ranges e citações existem nas transcrições recebidas?
7. Duração e densidade combinam com o formato sem sacrificar sentido?
8. Justificativas usam somente evidência disponível?

## Decisão

- `refined`: existe defeito corrigível somente na EDL; inclua a EDL completa revisada. O host grava a revisão e a nova `edl.draft.json` transacionalmente e executa outro loop.
- `approved`: todos os checks passaram e a revisão aponta para exatamente a última EDL validada. O host publica `edl.json`.
- `needs_human_review`: a evidência é insuficiente, as restrições são incompatíveis, uma tool falhou sem recuperação ou o limite de loops foi alcançado.

Não aprove por ausência de achado superficial. Não retorne apenas diffs; uma revisão refinada contém uma EDL completa para validação independente.

## Saída

Grave `review.001.json` via `write_artifact` com todos os campos obrigatórios:

```json
{
  "schema_version": 1,
  "artifact": "review",
  "iteration": 1,
  "edl_revision": 1,
  "status": "approved",
  "checklist": {
    "brief": true,
    "beats": true,
    "retakes": true,
    "redundancy": true,
    "continuity": true,
    "evidence": true,
    "pacing": true,
    "justifications": true
  },
  "findings": [],
  "summary": "A montagem preliminar cumpre o brief, removeu os retakes descartados e preservou a integridade e continuidade narrativa.",
  "revised_edl": null
}
```

Quando a montagem cumprir o objetivo editorial, use `status: "approved"` e `revised_edl: null`. Emita a chamada `write_artifact` diretamente.
