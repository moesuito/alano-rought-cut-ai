# Contrato de artefatos

Cada execução possui uma raiz isolada. Entradas são somente leitura; saídas JSON são validadas, versionadas e gravadas atomicamente pelo host.

## Estado editorial

| Artefato lógico | Escritor | Conteúdo | Próximo consumidor |
|---|---|---|---|
| `diagnosis.json` | diagnose | objetivo, incertezas, retakes, evidências e arquétipo | plan |
| `cut_plan.json` | plan | beats ordenados, candidatos, exclusões e pacing | assemble |
| `edl.draft.json` | assemble/review | ranges editoriais ainda não refinados acusticamente | review |
| `review.NNN.json` | review | checklist, achados e decisão de uma iteração | review/host |
| `edl.json` | host após aprovação | cópia imutável da EDL editorial aprovada | pipeline determinístico |
| `agent_run.json` | host | revisions, hashes, modelo, fases, tool calls e resultado | auditoria |
| `llm_usage.jsonl` | host | uso e latência por chamada, sem conteúdo privado | telemetria |

`NNN` começa em `001` e cresce monotonicamente. Uma revisão nunca substitui evidência histórica; o nome lógico aponta para a revisão válida mais recente e o ledger preserva a cadeia.

## Metadados

O host, não a LLM, registra para cada revisão:

- `run_id`, fase, iteração e horário;
- versão do conhecimento e schema;
- modelo e configuração não secreta;
- hashes dos inputs e do output;
- validação de schema e resultado da tool call.

Os payloads editoriais mantêm `version` ou `schema_version` para compatibilidade. Eles não incluem API key, headers, prompt completo, transcript completo ou raciocínio privado.

## Transições válidas

```text
diagnose -> ready | needs_human_review
plan -> ready | needs_human_review
assemble -> draft | needs_human_review
review -> refined -> nova revisão
review -> approved -> host publica edl.json
review -> needs_human_review
limite/erro -> needs_human_review
```

`edl.json` só nasce de uma revisão `approved` cujo `edl_revision` referencia exatamente a última `edl.draft.json` validada. Alterar diagnóstico, plano ou EDL invalida aprovações dependentes.

## Logs

Logs técnicos registram IDs, tamanhos, hashes, status, latência, contagens de tokens, finish reason, tools chamadas e códigos de erro. Conteúdo de brief/transcrição e respostas completas ficam fora dos logs por padrão. Exceções de debug exigem opt-in e sanitização.
