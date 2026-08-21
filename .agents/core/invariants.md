# Invariants

Mantenha estas regras em contexto durante todo o run.

- A entrega final é `<videos_dir>/edit/timeline.xml`.
- Artefatos de uma execução por skill ficam em `<videos_dir>/edit/`; execuções da CLI instalada usam a sessão correspondente em `%LOCALAPPDATA%\AlanoCut\sessions` e entregam o XML na pasta do usuário.
- Nunca grave mídia, transcrições ou segredos dentro do repositório.
- Nunca corte dentro de uma palavra.
- A precisão da saída nunca pode superar a precisão da evidência recebida.
- Não confie somente no ASR quando um boundary estiver apertado; use evidência acústica.
- A stack canônica é Whisper Vulkan + Wav2Vec2 DirectML + Pyannote ONNX DirectML, com DeepFilterNet no caminho acústico.
- WhisperX, ElevenLabs e AssemblyAI não pertencem a novos runs v0.6.
- Toda palavra normativa deve possuir intervalo temporal positivo e proveniência auditável.
- Caches só são válidos quando source hash, schema, modelos, configurações e versões relevantes correspondem.
- `takes_packed.md` é a leitura editorial primária; o JSON canônico de palavras é a autoridade temporal.
- A transcrição é o mapa. A LLM é a editora. Helpers determinísticos são os verificadores e executores.
- A LLM não inventa percepção audiovisual, fontes ou timestamps.
- O EDL é a autoridade editorial entre agente e pipeline técnico.
- XML referencia mídia original.
- O caminho de QA é audio-only e não adiciona acabamento.
- Preview é WAV PCM seco com timeline map.
- Execute sem pular ou reordenar: boundary refiner -> WAV/map -> audio QC -> semantic QC -> preview transcript -> transcript join QC -> readiness exit 0 -> XML.
- Qualquer relatório ausente/antigo, status diferente de `pass`, falha de parse ou readiness diferente de `0` bloqueia XML.
- Mudança material no EDL invalida todos os artefatos downstream.
- Falha de parse ou limite de loops não pode ser convertido em aprovação.
- `timeline_view.py` e `validate_edl_boundaries.py` são diagnósticos manuais legados, não substitutos dos gates.
- Não crie MP4 final, legendas, overlays, color grading, animações, publicação ou finishing.
- Pergunte ao usuário somente quando a ausência de informação puder prejudicar materialmente a edição.
