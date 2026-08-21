# Tarefa: montar a EDL preliminar

Objetivo: executar o plano com ranges reais e produzir uma montagem editorial revisável.

## Entradas

- `diagnosis.json` e `cut_plan.json` válidos;
- `takes_packed.md`;
- transcrições canônicas consultadas sob demanda;
- `edl_template.json` imutável fornecido pelo host com fontes, naming e frame rate;
- `contracts/edl.md`.

## Procedimento

1. Trabalhe beat por beat e selecione o take que satisfaz o critério do plano.
2. Leia a transcrição canônica antes de cortar dentro de uma frase compactada.
3. Mantenha ranges separados quando um reparo semântico combinar takes.
4. Leia mentalmente todas as junções; ajuste o conteúdo, não o waveform.
5. Calcule a duração e, se exceder o target, remova redundância e beats fracos antes de destruir clareza.
6. Preserve sem alterações os campos técnicos do template e valide fontes, timestamps, quotes, beat IDs e soma de duração.

Não aplique padding, thresholds de silêncio, frames, QC ou exportação.

## Saída

Grave `edl.draft.json` válido contra `schemas/edl.schema.json`. Se nenhum corte coerente puder cumprir uma restrição essencial, termine em revisão humana por meio do estado da execução; não fabrique uma EDL aprovada.
