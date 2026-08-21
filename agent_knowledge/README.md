# Conhecimento do agente editorial

Esta pasta é a biblioteca de runtime de **O Editor**, o agente embutido do Alano Cut. Ela contém somente conhecimento e contratos necessários para compreender transcrições prontas, planejar um rough cut, montar uma EDL e revisar a própria decisão. O instalador a distribui com a CLI global; ela não é copiada para a pasta dos vídeos nem operada como skill por Codex, Antigravity ou outro agente externo.

Ela não é uma configuração do agente de desenvolvimento. A separação é deliberada:

- `.agents/skills/`: skills do squad que desenvolve o produto;
- `agent_knowledge/`: inteligência carregável pelo agente editorial em runtime;
- helpers Python: orquestração, validação estrutural e estágios determinísticos.

O operador inicia o produto executando apenas `alanocut` na pasta de mídia. A TUI e o pipeline preparam as entradas; o agente editorial não inicializa workspaces nem controla o ambiente de desenvolvimento.

## Fronteira de responsabilidade

O agente editorial recebe `takes_packed.md`, brief e, quando necessário, trechos da transcrição canônica. Ele não inventa mídia, não transcreve, não alinha palavras, não analisa waveform, não refina frames, não executa QC e não exporta XML. Sua saída final é a EDL proprietária ainda sujeita aos gates determinísticos do pipeline.

## Carregamento

`manifest.json` é a fonte de verdade para carregamento seletivo. Na ponte de compatibilidade, o motor de três fases carrega identidade/core e o arquétipo indicado pela escolha explícita da TUI, mantendo seus JSONs legados. A escolha `custom` carrega somente o núcleo, sem tentar resolver um módulo chamado `custom`. No fluxo futuro, o diagnóstico persistirá `selected_archetype` e a fase de planejamento carregará esse único módulo. O executor de tools/artifacts acrescentará a tarefa e o schema da fase atual. As tasks e schemas desta pasta são o contrato preparado para essa evolução, não uma alegação de que o executor já os utiliza. Não carregue os dez arquétipos por padrão.

Ordem esperada:

1. `core/system_prompt.md`;
2. módulos `always` do manifesto;
3. contrato da entrada disponível;
4. tarefa e schema da fase;
5. um único arquétipo selecionado; no fluxo futuro, o diagnóstico roda sem arquétipo e `selected_archetype: null` mantém apenas o core.

Os schemas usam JSON Schema 2020-12 e fecham propriedades desconhecidas. Exemplos são ilustrativos; não são conhecimento normativo e não devem ser injetados quando o modelo já obedece ao schema.

## Artefatos de uma execução

Os nomes e as transições estão em `contracts/artifacts.md`. Em resumo:

```text
brief + takes_packed.md
  -> diagnosis.json
  -> cut_plan.json
  -> edl.draft.json
  -> review.NNN.json
  -> edl.json
```

Uma falha de parse, schema, evidência ou limite de loops termina em `needs_human_review`; nunca se converte silenciosamente em aprovação.
