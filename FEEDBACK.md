# Feedback de Evolução do Alanocut: Snapping por Waveform

Este documento reúne o diagnóstico de problemas de ritmo identificados nos cortes das aulas do módulo **08 - Conta e Cadastro** e as diretrizes arquiteturais para a implementação de um refinador automático de cortes baseado em Waveform e Redução de Ruído Neural.

---

## 1. O Diagnóstico do Problema

Atualmente, o `alanocut` confia exclusivamente nos timestamps de palavra fornecidos pelas APIs de transcrição de áudio (como o ElevenLabs Scribe). Isso gera dois tipos de falhas de cadência no *rough cut*:

1. **GAPs e Respiros Longos (Over-estimation):** O alinhador de voz (ASR) frequentemente estende o timestamp da última palavra de uma frase sobre o silêncio ou respiro subsequente do palestrante. 
   * *Exemplo na Aula 08.1:* A palavra *"salvar."* teve duração reportada de `69.04s` a `70.92s`. O respiro longo de quase 2 segundos ficou preso no corte.
   * *Exemplo na Aula 08.3:* O corte da frase de introdução estendeu-se até `66.76s`, deixando mais de 1 segundo de ar morto antes da próxima frase.
2. **Palavras Cortadas/Sílaba Ceifada (Under-estimation):** O alinhador encerra a palavra milissegundos antes do término físico da emissão sonora.
   * *Exemplo na Aula 08.3:* No corte em `00:01:10;09` na timeline (`471.73s` na bruta), a palavra *"confiança,"* foi cortada antes do som final (*"-ça"*). Foi necessário estender manualmente em 3 frames (`100ms`) até `00:01:10;12` para salvar a palavra.

---

## 2. A Solução Proposta: Snapping Waveform Automatizado

Para corrigir esses desvios e obter uma cadência profissional de edição (*Radio Edit* / *Jump Cut*), devemos implementar um refinamento baseado na forma de onda do áudio (Waveform), obedecendo às seguintes regras de cadência:

### A Cadência de Corte Ideal
Visão conceitual da linha do tempo:
`[----  ][-------]`

* **Out-point (Clip Anterior):** Termina a voz ativa (`----`), deixa **exatamente 2 frames de silêncio/ar** (`  `), e corta.
  * *Justificativa:* Preserva o decaimento natural das vogais e consoantes sibilantes sem deixar silêncio perceptível (cerca de `66ms` a 30fps).
* **In-point (Próximo Clip):** Começa imediatamente na modulação da voz (`-------`), com **0 frames de silêncio líder**.
  * *Justificativa:* Remove puxadas de ar/inspirações físicas do palestrante e cria um ritmo dinâmico que prende a atenção.

---

## 3. Pipeline de Processamento e Otimização de Performance

Em vez de renderizar vídeos pesados (MP4) para analisar os cortes, a pipeline de análise deve ser puramente baseada em áudio temporário de alta velocidade:

```
[Bruta Original] 
       │
       ▼ (Extração rápida via ffmpeg)
[Áudio Mono PCM (.wav)] 
       │
       ▼ (Processamento de Limpeza)
[ARNNDN / RNNoise (Denoise)] ➔ Zera o ruído de fundo (hiss, AC)
       │
       ▼ (Normalização de Volume)
[Loudnorm / EBU R128] ➔ Padroniza voz ativa a -16 LUFS / -1dB Peak
       │
       ▼ 
[Áudio Guia Otimizado] ➔ VAD estável com linha de corte constante (-35dBFS)
       │
       ▼
[Snapping Waveform] ➔ Algoritmo refina timestamps do EDL (In: 0 frames / Out: +2 frames)
       │
       ▼
[Timeline FCP XML] ➔ Aponta para o vídeo original com áudio intocado
```

### Detalhes Técnicos dos Filtros no FFMPEG

Para criar o áudio guia de análise, o Codex pode executar um único comando FFmpeg encadeado:

```bash
ffmpeg -i bruta.mov -af "arnndn=m=cb.rnn,loudnorm=I=-16:TP=-1.0:LRA=7" -ac 1 -ar 16000 audio_guia.wav
```

1. **`arnndn` (RNNoise):** Usa a rede neural recorrente nativa do FFmpeg para limpar todo o ruído de sala. O áudio resultante terá silêncio analógico real (`-90dB`) nos intervalos e apenas voz modulando.
2. **`loudnorm` (EBU R128):** Normaliza o áudio de entrada para `-16 LUFS` com pico em `-1.0dB`. Isso garante consistência de ganho.
3. **Análise de Waveform Simplificada:** Com a normalização e o denoise aplicados, uma linha de corte simples de **`-35dBFS`** se torna 100% confiável para detectar o limiar de voz ativa vs silêncio em qualquer áudio (independente de ter sido gravado alto ou baixo).

### Prevenção de Estalos (Pops)
Para garantir que o corte "em cima da voz" (0 frames de ar no In-point) não gere estalos acústicos causados pela descontinuidade da onda, o renderizador final ou o XML deve prever um fade-in/fade-out ultra-curto (sub-frame) de **5ms a 10ms** nas pontas de áudio de cada take.

---

## 4. Sugestão de Roteiro de Implementação para o Codex

1. **Fase 1 - Script de Extração & Preparação de Áudio:**
   * Implementar a extração rápida de áudio mono da bruta.
   * Integrar a limpeza `arnndn` + normalização `loudnorm`.
2. **Fase 2 - Algoritmo Snapper Waveform:**
   * Ler o `edl.json`.
   * Para cada range, carregar a vizinhança de áudio correspondente no `audio_guia.wav`.
   * Encontrar o ponto exato de início de voz (Onset) e ajustar o `start`.
   * Encontrar o ponto exato de fim de voz (Offset), adicionar `66ms` (2 frames), e ajustar o `end`.
   * Salvar os novos limites refinados no `edl.json`.
3. **Fase 3 - Exportação XML:**
   * Rodar o exportador FCP 7 XML usando os timestamps refinados.
