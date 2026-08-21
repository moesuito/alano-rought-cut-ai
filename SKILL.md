---
name: alano-rought-cut-ai
description: Create transcript-driven rough cuts from local media, refine exact audio boundaries, run deterministic QC, and export a Premiere-compatible Final Cut Pro 7 XML timeline.
---

# Alano Rough Cut AI

Este arquivo descreve o produto quando o repositório é invocado como skill. As instruções de desenvolvimento do repositório ficam em `AGENTS.md`.

Antes de editar mídia:

1. leia `.agents/core/invariants.md`;
2. leia `.agents/core/workflow.md`;
3. use o protocolo capaz em `.agents/core/capable_agent_protocol.md` por padrão;
4. leia as dez etapas em `.agents/steps/` e apenas o arquétipo compatível com o conteúdo;
5. preserve decisões e checkpoints em `edit/run_state.md` e `edit/project.md`.

O caminho canônico v0.6 usa transcrição local com Whisper Vulkan, alinhamento Wav2Vec2 DirectML e diarização Pyannote ONNX DirectML. Não selecione WhisperX, ElevenLabs ou AssemblyAI para novos runs v0.6.

A cadeia obrigatória depois da decisão editorial é:

```text
refinamento exato
  -> preview WAV e timeline map
  -> audio QC
  -> semantic QC
  -> transcrição canônica do preview
  -> transcript join QC
  -> readiness exit code 0
  -> XML
```

Qualquer evidência ausente, antiga, em revisão ou falha bloqueia o XML. O escopo é somente rough cut: não produza render final, legendas, overlays, color grading, animações ou publicação.
