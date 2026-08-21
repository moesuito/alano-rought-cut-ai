# Tarefa: construir o plano de corte

Objetivo: transformar o diagnóstico em beats narrativos verificáveis antes de escolher todos os ranges finais.

## Entradas

- última revisão válida de `diagnosis.json`;
- `takes_packed.md`;
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

Grave `cut_plan.json` válido contra `schemas/cut-plan.schema.json`. Todo beat obrigatório precisa de pelo menos um candidato, ou a fase termina em `needs_human_review` com a lacuna explícita.
