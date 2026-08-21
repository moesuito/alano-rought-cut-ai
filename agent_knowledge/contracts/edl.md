# Contrato da EDL editorial

A EDL proprietária representa **o que** entra e em qual ordem. O agente não fornece frames refinados, waveform evidence, relatório de QC ou XML.

O host entrega à fase de montagem `edl_template.json`, validado contra `schemas/edl-template.schema.json`, com `sources`, `sequence_fps` e os campos de naming já inferidos pela TUI/pipeline. O agente preenche beats, ranges, citações, justificativas e duração; não cria nem altera paths, frame rate ou identificadores técnicos. O host compara esses campos com o template antes de aceitar `edl.draft.json`. Se o template estiver ausente, inválido ou tiver sido alterado, a montagem falha fechada em vez de adivinhar.

## Regras

- `sources` mapeia cada ID usado para a referência opaca `source:<ID>` entregue pelo host; paths físicos permanecem fora do prompt e só entram na projeção técnica posterior.
- `ranges` está na ordem da timeline final.
- `start` e `end` são segundos na fonte e precisam corresponder a limites lexicais existentes, com `end > start`.
- `beat_id` liga cada range a um beat do plano. Um beat pode usar vários ranges e um range pertence a um beat.
- `quote` é citação literal do range; `reason` explica a função ou escolha de take em uma frase curta.
- Ranges de um reparo composto permanecem separados e explicam a correção semântica.
- `total_duration_s` é a soma de `end - start`, tolerada apenas a diferença de arredondamento.
- `required_beats` contém somente obrigações reais do brief/plano; não invente uma regra de produto para preencher a lista.

## Pacing e listas

`is_list` informa que a cadência de enumeração é editorialmente relevante. Ele não define limiar de silêncio. O refinador determinístico escolhe padding, separa gaps, faz snapping e adiciona frames.

## Integridade antes da revisão

1. Todas as fontes e ranges existem na evidência recebida.
2. Todos os ranges têm duração positiva e ordem narrativa intencional.
3. Cada `beat_id` existe no plano e nos `required_beats` quando obrigatório.
4. Citações sustentam a justificativa e não alteram o sentido original.
5. Retakes rejeitados e fala de produção não sobreviveram sem motivo explícito.
6. `total_duration_s` confere.
7. O nome da timeline usa contexto disponível; número ausente não é inventado.

Uma EDL editorial aprovada ainda pode ser rejeitada pelos estágios determinísticos. Essa rejeição não é uma opinião editorial e não deve ser contornada pelo agente.
