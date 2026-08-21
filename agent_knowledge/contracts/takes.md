# Contrato de takes e transcrições

## `takes_packed.md`

É o mapa editorial primário. Cada seção identifica uma fonte e cada linha contém um range de frase:

```text
## C0103  (duration: 2m 04.2s, 41 phrases)
  [002.42-006.85] S0 Texto pronunciado neste intervalo.
```

- O título após `##` é o `source` usado pelos artefatos.
- Os números entre colchetes são segundos na fonte, não na timeline final.
- `S0`, quando presente, é um identificador de speaker, não um nome ou papel inferido.
- Uma linha compactada pode conter várias palavras. Seus limites servem para planejamento e ranges inteiros.

Leia todas as fontes antes do diagnóstico. Não confunda ordem do arquivo com ordem narrativa e não deduza que a última versão é a correta sem comparar o conteúdo.

## `transcripts/<source>.json`

É uma entrada somente leitura e sob demanda. Consulte-a quando for preciso:

- cortar dentro de uma frase compactada;
- verificar a grafia ou o intervalo de uma palavra;
- confirmar speaker ou continuidade nos limites;
- resolver um conflito entre citação e timestamp.

Cada palavra editorialmente utilizável deve oferecer `text`, `start` e `end`, com intervalo positivo. Use exatamente os valores recebidos. Eventos, spacing e campos técnicos não são palavras inventáveis pelo agente.

## Referência de evidência

Artefatos anteriores ao EDL apontam para evidência com:

```json
{
  "source": "C0103",
  "start": 2.42,
  "end": 6.85,
  "quote": "Texto literal ou resumo identificado como tal"
}
```

No EDL, `quote` deve ser literal. Em diagnóstico e plano, um resumo é permitido, mas não pode introduzir uma afirmação ausente.

Se a fonte ou o intervalo não existir, a referência é inválida. Se o ASR estiver semanticamente ambíguo e outro take não resolver a dúvida, registre `needs_human_review`.
