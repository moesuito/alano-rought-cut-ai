# AGENTS.md — Alano Rough Cut AI

## Estado canônico

- Linha de desenvolvimento: `v0.6.0`.
- Branch canônica: `codex/feature-v0.6.0-agentic-editor`.
- Repositório de desenvolvimento: `O:\Antigravity\alano-cut`.
- Instalação global da CLI: `%APPDATA%\alano-rought-cut-ai`.
- Sessões, modelos e caches: `%LOCALAPPDATA%\AlanoCut`.
- Entrega do produto: `timeline.xml` no formato Final Cut Pro 7/XMEML, importável no Adobe Premiere Pro.

O Alano Rough Cut AI faz exclusivamente o primeiro corte editorial. Não implemente render final, legendas, motion design, color grading, trilha, publicação ou acabamento.

## Direção do produto

O produto é local-first. A meta é executar transcrição, análise acústica, raciocínio editorial, refinamento, QC e exportação na máquina do usuário.

O único componente que ainda pode depender de serviço remoto durante o desenvolvimento é a LLM editorial. O cliente usa o contrato OpenAI-compatible para permitir tanto APIs quanto servidores locais, como Ollama ou vLLM. Novas decisões não devem aumentar a dependência de nuvem.

A interface atual de terminal é deliberadamente funcional e provisória. Não invista em acabamento visual da TUI antes da decisão sobre a futura GUI desktop, possivelmente Electron ou Tauri.

## Pipeline v0.6

```text
mídia original
  -> DeepFilterNet 3
  -> Whisper local via Vulkan
  -> alinhamento Wav2Vec2 via DirectML
  -> diarização Pyannote ONNX via DirectML
  -> transcrição canônica palavra a palavra
  -> agente editorial multi-turno
       1. diagnóstico e estratégia
       2. plano de montagem e EDL preliminar
       3. crítica, revisão e novos loops
  -> refinamento determinístico de boundaries
  -> preview WAV e gates de QC
  -> readiness gate
  -> timeline.xml
```

Vulkan e DirectML formam o caminho Windows cross-vendor para GPUs AMD, NVIDIA e Intel, com fallbacks explicitamente documentados quando disponíveis. WhisperX, ElevenLabs e AssemblyAI não pertencem ao caminho canônico v0.6. Código legado desses provedores pode existir até uma limpeza controlada, mas não deve orientar novas features nem a documentação normativa.

## Responsabilidades do agente editorial

A LLM recebe a transcrição pronta. Ela não deve transcrever, analisar waveform ou inventar precisão audiovisual que não recebeu. Sua responsabilidade é:

1. compreender todo o conteúdo e o brief;
2. identificar estrutura, retakes, pickups, erros, cacos e intenção narrativa;
3. criar um plano editorial verificável;
4. converter o plano em ranges do EDL proprietário;
5. revisar criticamente a montagem em loops até aprovação válida;
6. entregar o EDL aos estágios determinísticos de áudio, QC e XML.

Modelos menores e locais são um requisito de arquitetura. Prefira tarefas menores, estado explícito, saídas estruturadas, validação e loops curtos em vez de depender de uma única chamada excepcionalmente inteligente.

## Prompts

- `helpers/prompts/agentic_editor_system_prompt.md`: persona, conhecimento e princípios editoriais do agente multi-turno. Deve permanecer editável fora do Python.
- `helpers/prompts/agentic_prompts.py`: composição das tarefas, schemas de saída e regras dinâmicas por formato.
- `helpers/prompts/editor_system_prompt.md`: prompt legado do modo one-shot, mantido enquanto esse modo existir.

Não volte a embutir o system prompt agêntico inteiro no código Python.

## Contratos editoriais e técnicos

- Nunca cortar dentro de palavra.
- A precisão da saída nunca pode superar a precisão da evidência recebida.
- Falas de bastidor são classificadas pela função comunicativa, não por palavras isoladas.
- Retakes devem ser resolvidos pela correção, completude, clareza e continuidade; recência é apenas desempate.
- Vídeos curtos e longos têm pacing diferente.
- O EDL é a autoridade editorial entre a LLM e o pipeline determinístico.
- XML só pode representar mídia original e uma EDL pronta.
- Estados ausentes, inválidos, `review`, `warning`, hashes antigos ou falhas de parse não podem virar aprovação implícita.

O workflow normativo do produto está em `.agents/core/invariants.md`, `.agents/core/workflow.md` e `.agents/steps/`. O código ainda possui gaps entre esse contrato e o orquestrador; trate-os como dívida conhecida até o próximo code review, não como comportamento aprovado.

## Protocolo de desenvolvimento

1. Leia este arquivo, `README.md`, `docs/ARCHITECTURE.md` e os documentos específicos da área alterada.
2. Preserve mudanças do usuário e dados fora do código rastreado.
3. Mantenha alterações pequenas, auditáveis e coerentes com a direção local-first.
4. Para código, execute os testes relevantes e, antes de concluir, a suíte completa quando o custo for proporcional ao risco.
5. Para documentação/metadados, execute ao menos `git diff --check` e verificações estruturais pertinentes.
6. Não anuncie contagens fixas de testes sem medi-las na mesma revisão.
7. Push, PR, merge, tag e release são ações distintas; só execute as autorizadas pelo usuário.

Quando um fluxo explícito de ORCHESTRATOR/WORKER for solicitado, leia `ORCHESTRATOR.md`, `WORKER.md` e `.agents/development/agent_operating_model.md`. Sem essa delegação explícita, trabalhe normalmente neste repositório.

## Sincronização da instalação local

Depois de validar uma mudança destinada a teste pelo usuário, sincronize a árvore rastreada relevante para `%APPDATA%\alano-rought-cut-ai`, preservando obrigatoriamente `.env`, `.venv` e `user-settings.json`. A fonte deve ser o checkout central `O:\Antigravity\alano-cut`, não um caminho antigo de worktree.

## Prioridades após a consolidação

1. Code review completo da v0.6 e inventário de dívidas.
2. Tornar o agente e o orquestrador estritamente fail-closed.
3. Evoluir o loop para planejamento, tarefas, execução, crítica e validação mais ricos.
4. Testar cortes reais em Reels, YouTube, videoaulas e VSLs.
5. Medir a qualidade com modelos locais menores.
6. Remover caminhos legados de nuvem/WhisperX somente após validar que nenhuma dependência ativa permanece.
7. Projetar a GUI desktop apenas quando o pipeline e seus contratos estiverem estáveis.
