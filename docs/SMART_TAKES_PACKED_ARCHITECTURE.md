# Arquitetura e Planejamento: Smart Takes Packager (Takes Packed Inteligente)

**Versão:** v0.6.0  
**Data:** 21 de Agosto de 2026  
**Status:** Proposta Aprovada / Próxima Implementação  

---

## 1. Contexto e Motivação

Nos testes com o modelo local (Ornith 1.0 35B), alimentar a LLM diretamente com o JSON palavra a palavra bruto gerou:
1. **Sobrecarga de Contexto:** Dezenas de milhares de micro-tokens estruturados com `start`, `end` e `confidence` para cada palavra individual.
2. **Instabilidade na Saída:** Dificuldade do modelo em serializar ranges extensos de EDL sem entrar em repetição infinita de tokens.

Por outro lado, o antigo `pack_transcripts.py` era simplista demais (apenas concatenava texto quando não encontrava tags legadas de `spacing`).

### A Solução Híbrida Ideal:
O **algoritmo determinístico local** (Python) consome a transcrição bruta palavra a palavra com precisão de milissegundos e produz o **Smart Takes Packed**. A **LLM editorial** recebe esse documento rico e limpo para tomar decisões de alto nível com velocidade máxima e zero alucinação.

---

## 2. Pilares do Novo Algoritmo de Empacotamento

O novo algoritmo (`SmartTakesPackager`) deve operar em 4 camadas de inteligência:

```
[ Transcrição Bruta Palavra a Palavra (Whisper + DirectML) ]
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                 SMART TAKES PACKAGER (Python)               │
├─────────────────────────────────────────────────────────────┤
│ 1. Análise de Silêncios e Gaps Temporais (gap >= 0.6s)      │
│ 2. Diarização e Troca de Falante (Speaker Separation)       │
│ 3. Delimitação Sintática / Pontuação Forte (. ? ! ...)      │
│ 4. Numeração e Indexação de Blocos de Take (TK_001, TK_002) │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
              [ Smart takes_packed.md / .json ]
                             │
                             ▼
             [ Agente Editorial LLM (Multi-Turno) ]
```

---

## 3. Regras de Segmentação do Smart Takes Packager

### Regra 1: Quebra por Silêncio Físico (Gap Detection)
* Sempre que o intervalo entre o término da palavra atual (`w[i-1].end`) e o início da próxima (`w[i].start`) for **$\ge 0.60$ segundos**, o bloco **obrigatoriamente se encerra**.
* Isso impede que frases ditas com minutos de diferença ou respiros longos fiquem fundidas na mesma linha.
* Se a pausa for superior a $2.0$ segundos, marcar explicitamente como `[PAUSA LONGA: X.Xs]`.

### Regra 2: Separação Rigorosa por Falante (Speaker Diarization)
* Qualquer alteração no `speaker_id` (SPEAKER_00 ➔ SPEAKER_01) inicia um novo bloco imediatamente.
* O cabeçalho do bloco deve identificar o locutor de forma legível: `[SPEAKER_00]` ou `[SPEAKER_01]`.

### Regra 3: Delimitação Sintática e Pontuação Forte
* Pontuação forte (`.`, `!`, `?`) combinada com um gap mínimo ($\ge 0.30s$) fecha a oração para manter frases completas em uma única unidade semântica.
* Vírgulas ou entonações contínuas sem pausa mantêm as palavras unidas.

### Regra 4: Identificadores Únicos de Take (Take IDs)
* Cada bloco gerado recebe um ID determinístico e estável, por exemplo: `[SRC_01 | TK_001 | 00:00:12.72 -> 00:00:24.10 | 11.4s]`.
* Isso permite que a LLM referencie blocos ou ranges usando os IDs de Take ou os timestamps já arredondados e validados.

---

## 4. Estrutura do Novo `takes_packed.md`

Exemplo do formato enriquecido que a LLM receberá:

```markdown
# SOURCE: SRC_66F16BBAFBCC0127 (video_principal.mp4)
Duração Total: 03m 18.8s | Falantes: SPEAKER_00

---
[TK_001] 00:00:00.00 -> 00:00:02.58 (2.6s) [SPEAKER_00]
Take 6, gravando.

--- [PAUSA: 10.1s] ---

[TK_002] 00:00:12.72 -> 00:00:24.10 (11.4s) [SPEAKER_00]
Olá, sejam muito bem-vindos ao treinamento completo da Ticto. Neste treinamento você vai aprender tudo sobre a plataforma, desde criar sua conta até gerenciar afiliados, área de membros e muito mais.

--- [PAUSA: 3.8s] ---

[TK_003] 00:00:27.91 -> 00:00:44.09 (16.2s) [SPEAKER_00]
A Ticto é uma plataforma completa de conversão e vendas online. Ela foi criada para quem quer vender produtos digitais, físicos, com a tecnologia de ponta, segurança e total autonomia. Muito além de um simples checkout, a Ticto reúne em um único lugar...

[TK_004] 00:00:45.78 -> 00:00:56.13 (10.4s) [SPEAKER_00]
Corta, volta. Só aquela parte ali do... É que está indo o script aí. Ah, tá. Aí, ó.
```

---

## 5. Benefícios Diretos para a LLM e o Pipeline

1. **Redução de ~80% nos Tokens de Contexto:** A LLM lê apenas o texto formatado por bloco em vez de dezenas de objetos JSON por segundo.
2. **Eliminação de Loops de Decodificação:** A LLM passa a operar com ranges de Take (`TK_002`, `TK_008`) ou intervalos consolidados de frase, gerando saídas de no máximo 1.000 a 2.500 tokens.
3. **Decisão Editorial Facilitada:** O modelo enxerga visualmente as pausas longas (`--- [PAUSA: 10.1s] ---`) e falas de bastidor isoladas em blocos próprios, facilitando a identificação de `must_avoid` e retakes.
4. **Precisão Preservada:** O algoritmo mantém o ponteiro exato de palavra (`words[start_idx].start` e `words[end_idx].end`), garantindo que o EDL gerado seja 100% frame-accurate.

---

## 6. Próximos Passos de Implementação

1. [ ] Reescrever `helpers/pack_transcripts.py` com o motor `SmartTakesPackager`.
2. [ ] Adicionar cálculo real de gaps entre palavras consecutivas (`w[i].start - w[i-1].end`).
3. [ ] Integrar metadados de diarização Pyannote/DirectML.
4. [ ] Atualizar os schemas e as instruções de prompt das Fases 1 a 4 para consumir o novo formato `takes_packed.md`.
5. [ ] Escrever suíte de testes unitários dedicada para validar as quebras de frases e pausas (`tests/test_smart_takes_packager.py`).
