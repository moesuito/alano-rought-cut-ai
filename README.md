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

O motor de compatibilidade v0.6 separa o trabalho em três fases:

1. diagnóstico global, retakes e estratégia narrativa;
2. plano concreto de montagem e EDL preliminar;
3. crítica da própria edição, revisão e novos ciclos até aprovação.

O conhecimento editorial fica em [`agent_knowledge/`](agent_knowledge/README.md), biblioteca modular distribuída com a instalação e carregada pelo runtime. [`agent_knowledge/manifest.json`](agent_knowledge/manifest.json) declara identidade, princípios, contratos, arquétipos, tasks e schemas. As skills em [`.agents/skills/`](.agents/skills/) pertencem exclusivamente ao squad que desenvolve o produto e nunca entram no contexto do editor.

Nesta reorganização, o motor atual preserva suas três fases e seus JSONs antigos por compatibilidade. O núcleo carregado foi tornado neutro para servir essa ponte; o motor usa identidade/núcleo e seleciona no máximo um arquétipo para o conteúdo. As tasks e schemas do futuro fluxo de quatro artefatos já podem ser validados pelo loader, mas ainda não são executados: isso depende da implementação do executor de tools e artefatos.

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
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Depois, em uma pasta com os vídeos:

```powershell
alanocut
```

Esse é o único comando público. A CLI não aceita subcomandos nem argumentos: a TUI detecta a mídia no diretório atual, pede o tipo de vídeo, aceita um briefing opcional e automatiza o pipeline. Existe um único `.venv` compartilhado na instalação global; cada execução cria uma sessão isolada em `%LOCALAPPDATA%\AlanoCut\sessions` e copia somente o `timeline.xml` final para a pasta onde `alanocut` foi executado.

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
- [`docs/AGENT_RUNTIME_TOOLS.md`](docs/AGENT_RUNTIME_TOOLS.md): tools mínimas e fronteiras do futuro executor.
- [`docs/LLM_CONTEXT_AND_TOKEN_BUDGET.md`](docs/LLM_CONTEXT_AND_TOKEN_BUDGET.md): telemetria e benchmark de modelos.
- [`docs/TRANSCRIPTION_SETUP.md`](docs/TRANSCRIPTION_SETUP.md): stack local de transcrição.
- [`docs/ROADMAP.md`](docs/ROADMAP.md): prioridades depois da consolidação.
- [`agent_knowledge/README.md`](agent_knowledge/README.md): biblioteca modular do agente editorial.

## Estado conhecido da v0.6

A branch contém a primeira implementação funcional do loop multi-turno. Ela ainda precisa de code review e testes reais mais amplos antes de ser tratada como release fechada.

As dívidas já conhecidas incluem:

- tornar parse, reflexão e limite de loops estritamente fail-closed;
- conectar todos os gates documentados ao orquestrador antes do XML;
- eliminar caminhos legados de transcrição em nuvem e WhisperX;
- ampliar planejamento, decomposição de tarefas, memória de trabalho e critérios editoriais para modelos locais menores;
- implementar o executor restrito de tools e artefatos antes de ativar o novo contrato de quatro fases;
- validar Reels, YouTube, videoaulas e VSLs com mídia real.

## Origem

Este repositório nasceu como uma adaptação de [browser-use/video-use](https://github.com/browser-use/video-use) e foi progressivamente especializado para rough cuts e integração com Premiere Pro. Consulte [`LICENSE`](LICENSE) para os termos preservados.
