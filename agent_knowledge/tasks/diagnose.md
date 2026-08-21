# Tarefa: diagnosticar o material

Objetivo: compreender o projeto inteiro antes de decidir a montagem.

## Entradas

- brief, quando fornecido;
- `takes_packed.md` completo;
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

Grave `diagnosis.json` válido contra `schemas/diagnosis.schema.json`. Use `ready` quando houver base suficiente para planejar; não use confiança alta para esconder evidência fraca.
