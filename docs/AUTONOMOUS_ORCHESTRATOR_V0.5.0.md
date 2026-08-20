# Alano Rough Cut AI — Arquitetura do Orquestrador Autônomo (v0.5.0)

Documento de especificação técnica e plano de arquitetura para a versão **v0.5.0**, tornando o `alanocut` um motor de corte bruto autônomo com inteligência artificial embutida (*Embedded Agent*), compatível com qualquer provedor **OpenAI-Compatible** (NVIDIA NIM, Ollama, vLLM, OpenAI, Groq, etc.).

---

## 1. Visão Geral e Objetivos

### 🎯 O Objetivo da Versão 0.5.0
Eliminar a necessidade de agentes de desenvolvimento externos (Antigravity, Claude Code, Codex) no fluxo de edição final. O usuário poderá rodar:

```bash
alanocut cut --brief "Aula 01 de introdução Ticto"
# ou
alanocut cut --brief "Corte 5 reels dinâmicos com ganchos fortes"
```

E o sistema executará todo o pipeline de ponta a ponta:
- **Tarefas Determinísticas (100% Código Python/GPU - Zero Tokens)**: inventário, redução de ruído, transcrição Whisper, alinhamento fonético Wav2Vec2, diarização Pyannote, agrupamento de frases, refinamento de bordas acústicas (*Snapper*), VAD, padding de respiração, validação QC e geração do XML.
- **Tarefa Cognitiva (Único tiro de LLM estruturado)**: compreensão do brief, eliminação de erros/falsos inícios e seleção semântica dos melhores takes.

---

## 2. Configuração de LLM Flexível (OpenAI-Compatible)

O orquestrador utilizará uma interface padronizada via variáveis de ambiente (`.env`) ou arquivo de configuração (`alanocut.json`), permitindo trocar facilmente o backend para **NVIDIA NIM**, modelos locais (**Ollama / vLLM**) ou nuvem:

```env
# Provedor LLM OpenAI-Compatible (NVIDIA NIM, Ollama, vLLM, OpenAI, Groq)
LLM_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=meta/llama-3.1-70b-instruct
```

---

## 3. Os Dois Modos de Ingestão e Montagem

O sistema foi desenhado para lidar nativamente com duas realidades comuns de edição:

```mermaid
flowchart TD
    A[Vídeos Brutos em raw_video/] --> B[Transcrição Unificada na GPU\nDeepFilterNet3 + Whisper + Wav2Vec2 + Pyannote]
    B --> C[takes_packed.md]
    
    C --> D{Tipo de Solicitação no Brief}
    
    D -->|Modo 1: Vídeo Único de Múltiplos Brutos| E[LLM Editor Único\n(Analisa todos os takes e monta 1 história)]
    E --> F[edl.json]
    F --> G[Snapper Acústico + Audio QC + timeline.xml]
    
    D -->|Modo 2: Lote / Múltiplos Vídeos\nEx: 5 Reels ou Múltiplas Aulas| H[Dispatcher / Video Planner (LLM)\nIdentifica e particiona os N vídeos]
    H --> I1[Micro-Brief Vídeo 01]
    H --> I2[Micro-Brief Vídeo 02]
    H --> I3[Micro-Brief Vídeo N]
    
    I1 --> J1[Sub-Agent Worker 01 (Contexto Limpo)\nGera edl_01.json -> timeline_01.xml]
    I2 --> J2[Sub-Agent Worker 02 (Contexto Limpo)\nGera edl_02.json -> timeline_02.xml]
    I3 --> J3[Sub-Agent Worker N (Contexto Limpo)\nGera edl_N.json -> timeline_N.xml]
```

---

### 🔹 Modo 1: Vídeo Único a partir de Múltiplos Arquivos Brutos (Composite Take Workflow)
*Exemplo: O caso da aula Ticto (`C006.mov`, `C007.mov`, `C008.mov`), onde `C008` é uma regravação do início e `C006` contém o corpo e o final.*

- **Como o Orquestrador opera**:
  1. Processa todos os arquivos brutos e une as transcrições em um único `takes_packed.md` com IDs de fonte explícitos (`C006`, `C007`, `C008`).
  2. Envia um único payload para a LLM com o brief do vídeo.
  3. A LLM seleciona os takes vencedores entre os diferentes arquivos (ex: intro de `C008` + miolo de `C006`).
  4. O pós-processamento algorítmico gera **uma única timeline final** (`timeline.xml`).

---

### 🔹 Modo 2: Lote / Múltiplos Vídeos Independentes (Multi-Video Batch Dispatcher)
*Exemplo: O usuário joga 10 arquivos brutos e pede: "Corte os 5 Reels mais interessantes dessas gravações" ou "Crie a Aula 1 e a Aula 2 separadas".*

Para evitar estourar o limite de tokens, alucinações por contexto poluído ou confusão entre vídeos, adotamos a arquitetura de **2 Camadas com Contextos Isolados**:

1. **Camada 1: O Dispatcher / Video Planner (Planejador Geral)**:
   - Faz uma chamada rápida de LLM que analisa o `takes_packed.md` geral e o brief.
   - Identifica a quantidade $N$ de vídeos a serem produzidos e quais trechos/fontes pertencem a cada vídeo.
   - Cria $N$ micro-arquivos estruturados (ex: `edit/batch/video_01_brief.json`, `edit/batch/video_02_brief.json`...).

2. **Camada 2: O Worker de Edição Isolado (Clean Context Per Video)**:
   - O orquestrador executa uma chamada de LLM dedicada para o Vídeo 1 (apenas com o seu micro-brief e as falas relevantes).
   - A LLM cospe a EDL do Vídeo 1 $\rightarrow$ o sistema roda Snapper + QC $\rightarrow$ exporta `timeline_01.xml`.
   - **O contexto é descartado e liberado da memória**.
   - O orquestrador carrega o Vídeo 2 em um contexto 100% limpo e repete o processo até concluir os $N$ vídeos.

---

## 4. O Pipeline Detalhado em 3 Fases

### 🟢 FASE 1: Ingestão e Processamento Acústico Local (Zero Tokens)
1. **Inventory**: Leitura de metadados (`ffprobe`, resolução, FPS, canais de áudio).
2. **Audio Denoising**: DeepFilterNet 3 com redução de 100 dB.
3. **ASR Transcription**: Whisper Large v3 Turbo rodando via Vulkan na GPU.
4. **Forced Alignment**: Wav2Vec2 CTC (`jonatasgrosman/wav2vec2-large-xlsr-53-portuguese`) via DirectML GPU, cravando os timestamps de palavras no milissegundo acústico real.
5. **Diarization**: Pyannote Diarization v3 via ONNX DirectML na GPU.
6. **Pack Transcripts**: Geração do `takes_packed.md` compacto.

---

### 🟡 FASE 2: Decisão Editorial Cognitiva (OpenAI-Compatible Call)
- O sistema faz uma requisição HTTP direta (`POST /v1/chat/completions`) com JSON Mode / Structured Output.
- **Prompt do Sistema**:
  - Regras editoriais por tipo de vídeo:
    - *Short-Form (Reels/TikTok/Shorts)*: gap de 200ms, sem filtro de lista, duração máxima $\le$ 90s, ritmo acelerado.
    - *Long-Form (Aulas/YouTube)*: gap padrão de 350ms, filtro de lista/enumeração de 500ms (`is_list: true`), ritmo natural.
- **Entrada**: `takes_packed.md` + Brief.
- **Saída**: JSON array com a lista de trechos escolhidos e seus beats.

---

### 🔵 FASE 3: Refinamento Acústico, QC e Exportação (Zero Tokens)
1. **`refine_edl_boundaries.py`**:
   - Aplica VAD nas formas de onda originais.
   - Adiciona pré-roll e tail-padding ($\ge$ 66ms / 2 frames).
   - Divide pausas $> 350$ms (ou $> 500$ms para listas / $> 200$ms para Reels).
2. **`preview_audio_qc.py` & `render.py`**:
   - Renderiza `preview.wav`.
   - Valida clipping de voz, estalos e descontinuidades de fase.
3. **`edl_to_fcpxml.py`**:
   - Gera o XML Final Cut Pro 7 (`timeline.xml` ou `timeline_NN.xml`) pronto para importar no Adobe Premiere Pro.

---

## 5. Próximos Passos de Implementação (Roadmap v0.5.0)

1. [x] Criação da branch `codex/feature-v0.5.0-autonomous-orchestrator`.
2. [x] Atualização do `.env.example` com suporte a `LLM_API_KEY`, `LLM_BASE_URL` e `LLM_MODEL`.
3. [x] Documentação formal da arquitetura e dos modos de vídeo único vs. lote de múltiplos vídeos.
4. [ ] Implementação do cliente Python leve `helpers/llm_client.py` (com suporte a NVIDIA NIM, Ollama, OpenAI).
5. [ ] Implementação do orquestrador principal `helpers/orchestrator.py` / comando `alanocut cut`.
6. [ ] Suporte ao Dispatcher para lotes de múltiplos vídeos (Reels / Shorts em batch).
7. [ ] Validação com a API Key do NVIDIA NIM.
