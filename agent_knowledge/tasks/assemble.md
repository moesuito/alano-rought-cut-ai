# Tarefa: montar a EDL preliminar

Objetivo: executar o plano com ranges reais e produzir uma montagem editorial revisável.

## Entradas

- `diagnosis.json` e `cut_plan.json` válidos;
- `transcripts/<source>.json` de cada fonte;
- `edl_template.json` imutável fornecido pelo host com fontes, naming e frame rate;
- `contracts/edl.md`.

## Procedimento

1. Trabalhe beat por beat e selecione o take que satisfaz o critério do plano.
2. Identifique e descarte cacos de início de take (como "Beleza", "Tá", "Ok"), posicionando `start` na primeira palavra útil da fala.
3. Mantenha ranges separados quando um reparo semântico combinar takes.
4. Leia mentalmente todas as junções; ajuste o conteúdo, não o waveform.
5. Calcule a duração e, se exceder o target, remova redundância e beats fracos antes de destruir clareza.
6. Preserve sem alterações os campos técnicos do template e valide fontes, timestamps, quotes, beat IDs e soma de duração.

Não aplique padding, thresholds de silêncio, frames, QC ou exportação.

## Saída

Grave `edl.draft.json` via `write_artifact` com todos os campos obrigatórios:

```json
{
  "version": 1,
  "metadata": {
    "timeline_name": "videoaula_ticto",
    "video_type": "videoaula",
    "content_number": null,
    "content_slug": "videoaula",
    "sequence_fps": "30000/1001",
    "diagnosis_revision": 1,
    "plan_revision": 1,
    "required_beats": []
  },
  "sources": {
    "SRC_66F16BBAFBCC0127": "source:SRC_66F16BBAFBCC0127"
  },
  "ranges": [
    {
      "id": "RANGE_1",
      "source": "SRC_66F16BBAFBCC0127",
      "start": 127.58,
      "end": 133.00,
      "beat_id": "BEAT_1",
      "quote": "Olá, sejam muito bem-vindos ao treinamento completo da Ticto.",
      "reason": "Abertura com dicção clara e ritmo fluido.",
      "is_list": false
    }
  ],
  "total_duration_s": 5.42
}
```

Copie as `sources` exatas de `edl_template.json`. Copie os timestamps `start` e `end` exatos das palavras lidas em `transcripts/<source>.json`. O campo `quote` DEVE ser exatamente o texto literal das palavras contidas entre `start` e `end`. Garanta que `total_duration_s` seja igual à soma de todos os ranges (`end - start`). Emita `write_artifact` diretamente.
