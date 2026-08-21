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
  -> agente editorial artifact-driven
       diagnose -> diagnosis.json
       plan -> cut_plan.json
       assemble -> edl.draft.json
       review -> review.NNN.json -> aprovação ou nova revisão
  -> EDL editorial imutável
  -> boundary refiner determinístico
  -> preview WAV + quatro relatórios de QC
  -> readiness gate
  -> timeline.xml
```

O caminho de mídia é local. A LLM editorial usa uma API OpenAI-compatible: durante o desenvolvimento ela pode apontar para um serviço remoto, mas a arquitetura também aceita servidores locais como Ollama ou vLLM. O objetivo é validar o produto com modelos menores executados na máquina do usuário.

## Agente editorial

O agente recebe a transcrição pronta e trabalha somente na decisão editorial. Ele não transcreve, não faz análise acústica e não inventa percepção visual.

O motor ativo da v0.6 separa o trabalho em quatro fases:

1. `diagnose`: compreensão global, retakes, incertezas e arquétipo;
2. `plan`: beats, ordem narrativa, candidatos e exclusões;
3. `assemble`: ranges apoiados na transcrição canônica;
4. `review`: crítica estruturada, correção da EDL e novos loops até aprovação válida.

O conhecimento editorial fica em [`agent_knowledge/`](agent_knowledge/README.md), biblioteca modular distribuída com a instalação e carregada pelo runtime. [`agent_knowledge/manifest.json`](agent_knowledge/manifest.json) declara identidade, princípios, contratos, arquétipos, tasks e schemas. As skills em [`.agents/skills/`](.agents/skills/) pertencem exclusivamente ao squad que desenvolve o produto e nunca entram no contexto do editor.

O executor reconstrói o contexto em cada fase a partir do conhecimento mínimo, inputs imutáveis e artefatos predecessores. A LLM interage somente por `read_file`, `read_artifact` e `write_artifact`; não recebe shell, paths físicos ou acesso genérico ao filesystem. O fluxo público não faz fallback para o motor antigo de três prompts nem para o modo one-shot.

A EDL aprovada pela revisão fica imutável em `edit/agent/artifacts/edl.json`. O host cria separadamente `edit/edl.json`, a projeção técnica com os paths necessários ao refinamento, QC e XML. A LLM vê apenas referências opacas como `source:SRC_...`; o registro que associa esses IDs à mídia permanece host-only.

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

Esse é o único comando público. A CLI não aceita subcomandos nem argumentos: a TUI detecta a mídia no diretório atual, pede o tipo de vídeo, aceita um briefing opcional e automatiza o pipeline. Todos os arquivos detectados pertencem à mesma operação; por exemplo, três gravações podem ser takes/fontes de um único vídeo final.

Existe um único `.venv` compartilhado na instalação global; cada execução cria uma sessão isolada em `%LOCALAPPDATA%\AlanoCut\sessions`. `timeline.xml` só é publicado atomicamente na pasta operada quando a revisão editorial, o refinamento, os quatro QCs e o readiness terminam em `pass`. Estado `review` ou warning bloqueante resulta em `needs_human_review`; falha técnica resulta em `failed`. Nenhum desses estados publica XML novo.

O terminal interativo atual é uma interface provisória para validar o pipeline. A futura GUI desktop ainda será decidida entre alternativas como Electron e Tauri; não há compromisso de framework nesta versão.

## Configuração da LLM

A configuração vem somente do `.env` da instalação confiável ou de variáveis de ambiente do processo. Arquivos `.env` e `alanocut.json` na pasta de mídia não podem redirecionar o provedor:

```dotenv
LLM_API_KEY=
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=z-ai/glm-5.2
```

Para um servidor local OpenAI-compatible, substitua `LLM_BASE_URL` e `LLM_MODEL` na instalação confiável. Endpoints remotos exigem HTTPS; HTTP é aceito somente em loopback. Servidores locais que não exigem autenticação podem usar um valor sentinela em `LLM_API_KEY` enquanto o cliente exigir o campo.

## Documentação

- [`AGENTS.md`](AGENTS.md): direção do produto e regras de desenvolvimento.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): componentes e contratos.
- [`docs/AGENTIC_LOOP_V0.6.0.md`](docs/AGENTIC_LOOP_V0.6.0.md): loop artifact-driven e estados fail-closed.
- [`docs/AGENT_RUNTIME_TOOLS.md`](docs/AGENT_RUNTIME_TOOLS.md): tools, allowlists e store versionado do executor.
- [`docs/LLM_CONTEXT_AND_TOKEN_BUDGET.md`](docs/LLM_CONTEXT_AND_TOKEN_BUDGET.md): telemetria e benchmark de modelos.
- [`docs/TRANSCRIPTION_SETUP.md`](docs/TRANSCRIPTION_SETUP.md): stack local de transcrição.
- [`docs/ROADMAP.md`](docs/ROADMAP.md): prioridades depois da consolidação.
- [`agent_knowledge/README.md`](agent_knowledge/README.md): biblioteca modular do agente editorial.

## Estado conhecido da v0.6

A branch contém o executor artifact-driven integrado ao caminho público. Ela ainda precisa de code review e testes editoriais reais mais amplos antes de ser tratada como release fechada.

As dívidas já conhecidas incluem:

- eliminar caminhos legados de transcrição em nuvem e WhisperX;
- ampliar planejamento, decomposição de tarefas e critérios editoriais para modelos locais menores;
- validar Reels, YouTube, videoaulas e VSLs com mídia real;
- implementar retomada automática após crash e uma política segura para mídia silenciosa sem palavras canônicas;
- definir recuperação operacional para lock abandonado sem transformar estado incerto em aprovação.

## Origem

Este repositório nasceu como uma adaptação de [browser-use/video-use](https://github.com/browser-use/video-use) e foi progressivamente especializado para rough cuts e integração com Premiere Pro. Consulte [`LICENSE`](LICENSE) para os termos preservados.
