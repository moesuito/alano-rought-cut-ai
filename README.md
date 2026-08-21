# Alano Rough Cut AI

Motor local-first de rough cut orientado por transcrição, com agente editorial multi-turno e exportação de timeline para Adobe Premiere Pro.

Linha atual de desenvolvimento: **v0.6.0 — Agentic Editor**.

## Objetivo

O sistema recebe mídia bruta, produz uma transcrição canônica, pensa como um editor sênior, monta um EDL proprietário, refina cortes com evidência acústica e exporta `timeline.xml` no padrão Final Cut Pro 7/XMEML.

O escopo termina no rough cut. O projeto não faz render final, legendas, motion design, color grading, trilha ou publicação.

## Arquitetura atual

```text
Mídia original
  -> DeepFilterNet 3
  -> Whisper local via Vulkan
  -> Wav2Vec2 forced alignment via DirectML
  -> Pyannote ONNX diarization via DirectML
  -> transcrição palavra a palavra
  -> agente editorial multi-turno
       diagnóstico e estratégia
       montagem da EDL
       crítica e refinamento em loops
  -> boundary refiner determinístico
  -> preview WAV + QC
  -> readiness gate
  -> timeline.xml
```

O caminho de mídia é local. A LLM editorial usa uma API OpenAI-compatible: durante o desenvolvimento ela pode apontar para um serviço remoto, mas a arquitetura também aceita servidores locais como Ollama ou vLLM. O objetivo é validar o produto com modelos menores executados na máquina do usuário.

## Agente editorial

O agente recebe a transcrição pronta e trabalha somente na decisão editorial. Ele não transcreve, não faz análise acústica e não inventa percepção visual.

O loop v0.6 separa o trabalho em três fases:

1. diagnóstico global, retakes e estratégia narrativa;
2. plano concreto de montagem e EDL preliminar;
3. crítica da própria edição, revisão e novos ciclos até aprovação.

O conhecimento/persona do agente fica em [`helpers/prompts/agentic_editor_system_prompt.md`](helpers/prompts/agentic_editor_system_prompt.md). As mensagens de tarefa e os schemas JSON ficam em [`helpers/prompts/agentic_prompts.py`](helpers/prompts/agentic_prompts.py). Essa separação é intencional para permitir evolução editorial sem reescrever o motor Python.

O modo one-shot continua disponível apenas como compatibilidade enquanto a migração para o loop agêntico é validada.

## GPU local

A stack Windows busca um caminho cross-vendor:

- Whisper.cpp/Vulkan para ASR local;
- DirectML para forced alignment e diarização ONNX;
- suporte a GPUs AMD, NVIDIA e Intel sem manter uma implementação editorial diferente por fabricante;
- fallback de CPU apenas quando o contrato do componente o permite e o resultado continua auditável.

WhisperX, ElevenLabs e AssemblyAI são caminhos legados e não fazem parte da configuração canônica v0.6. O código de compatibilidade ainda será removido após o próximo code review confirmar suas dependências.

## Uso atual

No Windows, a instalação global fica em `%APPDATA%\alano-rought-cut-ai` e as sessões/caches ficam em `%LOCALAPPDATA%\AlanoCut`.

```powershell
.\install.ps1 -Provider whisper-vulkan
```

Depois, em uma pasta com os vídeos:

```powershell
alanocut
```

O terminal interativo atual é uma interface provisória para validar o pipeline. A futura GUI desktop ainda será decidida entre alternativas como Electron e Tauri; não há compromisso de framework nesta versão.

## Configuração da LLM

Use `.env` ou variáveis de ambiente:

```dotenv
LLM_API_KEY=
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=z-ai/glm-5.2
```

Para um servidor local OpenAI-compatible, substitua `LLM_BASE_URL` e `LLM_MODEL`. Servidores locais que não exigem autenticação podem usar um valor sentinela em `LLM_API_KEY` enquanto o cliente ainda exigir o campo.

## Documentação

- [`AGENTS.md`](AGENTS.md): direção do produto e regras de desenvolvimento.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): componentes e contratos.
- [`docs/AGENTIC_LOOP_V0.6.0.md`](docs/AGENTIC_LOOP_V0.6.0.md): estado do agente multi-turno.
- [`docs/TRANSCRIPTION_SETUP.md`](docs/TRANSCRIPTION_SETUP.md): stack local de transcrição.
- [`docs/ROADMAP.md`](docs/ROADMAP.md): prioridades depois da consolidação.
- [`.agents/core/workflow.md`](.agents/core/workflow.md): workflow normativo de edição e gates.

## Estado conhecido da v0.6

A branch contém a primeira implementação funcional do loop multi-turno. Ela ainda precisa de code review e testes reais mais amplos antes de ser tratada como release fechada.

As dívidas já conhecidas incluem:

- tornar parse, reflexão e limite de loops estritamente fail-closed;
- conectar todos os gates documentados ao orquestrador antes do XML;
- eliminar caminhos legados de transcrição em nuvem e WhisperX;
- ampliar planejamento, decomposição de tarefas, memória de trabalho e critérios editoriais para modelos locais menores;
- validar Reels, YouTube, videoaulas e VSLs com mídia real.

## Origem

Este repositório nasceu como uma adaptação de [browser-use/video-use](https://github.com/browser-use/video-use) e foi progressivamente especializado para rough cuts e integração com Premiere Pro. Consulte [`LICENSE`](LICENSE) e [`NOTICE`](NOTICE) para os termos e créditos preservados.
