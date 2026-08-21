# Arquitetura v0.6.0

Atualizado em 2026-08-20.

## Princípios

1. **Local-first:** mídia, transcrição, análise acústica, EDL, QC e XML permanecem na máquina do usuário.
2. **LLM isolada da mecânica:** a LLM decide a edição; helpers determinísticos validam tempo, áudio e exportação.
3. **EDL como contrato:** o JSON proprietário é a fronteira entre raciocínio editorial e execução técnica.
4. **Evidência auditável:** decisões temporais precisam apontar para palavras, frames, samples e hashes reais.
5. **Fail-closed:** ausência, parse inválido, revisão pendente ou relatório antigo deve bloquear exportação.
6. **Cross-vendor no Windows:** Vulkan e DirectML evitam uma pipeline editorial separada para AMD, NVIDIA e Intel.

## Componentes

### Entrada e sessão

- `helpers/interactive_cli.py`: terminal interativo provisório.
- `helpers/orchestrator.py`: coordena o run de vídeo único.
- `helpers/session_manager.py`: mantém sessões em `%LOCALAPPDATA%\AlanoCut\sessions` e copia somente a entrega final para a pasta do usuário.

### Transcrição local

- `helpers/transcribe.py` e `helpers/transcribe_batch.py`: entrada do pipeline de transcrição.
- `helpers/deepfilter_audio.py`: denoising para análise/ASR.
- `helpers/whisper_vulkan.py`: Whisper local acelerado por Vulkan.
- `helpers/wav2vec2_directml.py`: alinhamento CTC palavra a palavra.
- `helpers/pyannote_directml.py`: diarização ONNX via DirectML.
- `helpers/transcription_contract.py`: schema canônico e proveniência.
- `helpers/pack_transcripts.py`: visão editorial condensada em `takes_packed.md`.

WhisperX e provedores cloud ainda aparecem em módulos de compatibilidade, mas estão fora do caminho canônico v0.6 e devem ser removidos somente após análise de dependências.

### Inteligência editorial

- `helpers/agentic_editor.py`: conversa persistente e loop multi-turno.
- `helpers/prompts/agentic_editor_system_prompt.md`: persona e conhecimento editorial.
- `helpers/prompts/agentic_prompts.py`: tarefas, contexto dinâmico e schemas JSON.
- `helpers/llm_client.py`: cliente OpenAI-compatible para backends remotos ou locais.

O agente executa estratégia, montagem e crítica. A direção pretendida é evoluir para planejamento e tarefas explícitas, com estado verificável e loops que funcionem em modelos locais menores.

### EDL, áudio e QC

- `helpers/refine_edl_boundaries.py`: converte intenção editorial em boundaries acústicos/frame-exact.
- `helpers/render.py`: gera preview WAV seco e `preview_timeline.json`.
- `helpers/preview_audio_qc.py`: verifica entradas, saídas, joins, clipping e pops.
- `helpers/semantic_qc.py`: verifica beats obrigatórios.
- `helpers/preview_transcript_qc.py`: compara a fala esperada e observada em cada join.
- `helpers/verify_edit_ready.py`: gate agregado de prontidão.
- `helpers/edl_to_fcpxml.py`: exporta FCP7/XMEML referenciando a mídia original.

## Fluxo de dados

```text
source media + brief
  -> canonical transcripts
  -> takes_packed.md
  -> editorial_strategy.json
  -> preliminary edl.json
  -> reflected/revised edl.json
  -> frame-exact edl.json + refine report
  -> preview.wav + timeline map
  -> audio/semantic/transcript QC reports
  -> readiness
  -> timeline.xml
```

## Limite da LLM

A LLM pode escolher, ordenar e justificar conteúdo. Ela não pode:

- inventar timestamps ou fontes;
- aprovar JSON que não foi interpretado;
- substituir validação acústica;
- transformar ausência de evidência em sucesso;
- exportar XML diretamente sem passar pelo contrato do EDL e pelos gates.

## Estado de implementação

A arquitetura acima é o contrato v0.6. A implementação atual ainda não conecta todos os gates ao caminho autônomo e possui fallbacks fail-open conhecidos. Esses gaps são prioridade do próximo code review e não devem ser descritos como garantias já entregues.

## Interfaces

A TUI existe apenas para teste funcional. A GUI desktop será desenhada depois que o pipeline, o agente e os contratos estiverem estáveis. Electron e Tauri são possibilidades, não decisões atuais.
