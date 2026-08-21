# PRD — Alano Cut v0.6

Status: documento vivo.
Atualizado: 2026-08-20.

## Visão

Transformar gravações brutas de talking head, Reels, YouTube, videoaulas, tutoriais e VSLs em um rough cut coerente, tecnicamente seguro e importável no Premiere Pro, com processamento local e um agente editorial compatível com modelos menores.

## Usuários

- Editor de vídeo que precisa eliminar retakes, falsos inícios, bastidores, redundância e pausas indevidas sem perder intenção.
- Criador/produtor que quer receber uma timeline editável, não um render final fechado.
- Agente de IA que precisa de transcrição compacta, ferramentas determinísticas, estado explícito e gates auditáveis.

## Requisitos

### CUT-00 — Transcrição local canônica

- Fonte e preview SHALL usar a stack local canônica: Whisper Vulkan, alinhamento Wav2Vec2 DirectML e, quando habilitada, diarização Pyannote ONNX DirectML.
- Cada palavra usada editorialmente SHALL possuir intervalo temporal positivo e proveniência de modelo/runtime.
- Cache SHALL estar vinculado ao SHA-256 da fonte, schema, modelos, configurações e versões relevantes.
- Ausência de timing, desalinhamento, cache incompatível ou fallback silencioso SHALL bloquear o fluxo.
- WhisperX, ElevenLabs e AssemblyAI SHALL permanecer fora do caminho canônico v0.6.

### CUT-01 — Agente editorial multi-turno

- A LLM SHALL receber a transcrição pronta e SHALL decidir apenas conteúdo, estrutura, retakes, pacing e montagem.
- O agente SHALL separar estratégia, montagem e crítica em estado explícito.
- Toda EDL SHALL ser justificável pelo brief, estratégia e transcrição.
- Falha de parse, schema inválido ou limite de loops sem aprovação SHALL produzir falha/revisão, nunca aprovação implícita.
- Identidade, princípios e arquétipos SHALL permanecer em `agent_knowledge/`, separados do runtime Python e das skills de desenvolvimento.
- O motor de compatibilidade de três fases SHALL preservar seus formatos atuais enquanto o executor de tools/artefatos não existir.
- Tasks e schemas do novo fluxo SHALL NOT ser tratados como executáveis apenas porque o manifesto consegue validá-los.

### CUT-02 — EDL e XML

- O EDL proprietário SHALL ser a autoridade entre a LLM e os helpers determinísticos.
- `metadata.sequence_fps` e `metadata.required_beats` SHALL existir; ranges SHALL apontar para fontes/timestamps reais e beats válidos.
- Somente uma EDL pronta SHALL ser exportada como FCP7/XMEML.
- O XML SHALL referenciar a mídia original e não SHALL representar um render final.

### CUT-03 — Boundaries seguros

- O refiner SHALL preservar palavras pretendidas e ajustar boundaries com ASR, waveform e frames exportáveis.
- Cortes não SHALL ocorrer dentro de palavras.
- Cada entrada, saída e join SHALL possuir evidência auditável.
- Atividade rejeitada, silêncio excessivo, ataque apertado, tail danificada, clipping ou pop grave SHALL bloquear exportação.

### CUT-04 — Cobertura e QC

- Beats obrigatórios SHALL ser verificados contra fonte, ranges e preview.
- Preview SHALL ser um WAV PCM seco com timeline map, não um render final.
- Preview transcription SHALL estar vinculada ao hash do WAV e possuir timing palavra a palavra.
- Audio QC e transcript QC SHALL cobrir todos os joins.
- Mudança material no EDL SHALL invalidar todos os artefatos downstream.

### CUT-05 — Workflow fail-closed

- O caminho obrigatório SHALL ser: `refine -> render WAV/map -> audio QC -> semantic QC -> preview transcript -> transcript join QC -> readiness -> XML`.
- Readiness SHALL retornar `0` para os artefatos atuais.
- Estado `review`, `warning`, `fail`, relatório ausente, hash antigo ou comando manual warning-only SHALL bloquear XML no fluxo autônomo.

### CUT-06 — Local-first e hardware

- Transcrição, análise acústica, EDL, QC e exportação SHALL executar localmente.
- O caminho Windows SHALL aceitar GPUs AMD, NVIDIA e Intel por Vulkan/DirectML sem lógica editorial específica por fabricante.
- A LLM SHALL usar um contrato OpenAI-compatible que permita backends remotos durante desenvolvimento e servidores locais no produto-alvo.
- Novas features SHALL evitar dependência adicional de nuvem.

### CUT-07 — Dados privados

- Mídia, transcrições, sessões e regressões privadas SHALL permanecer fora do Git.
- Segredos SHALL permanecer em `.env`/ambiente e não aparecer em argv, configs rastreadas, logs, relatórios ou exceções.

### CUT-08 — Interface operacional única

- O operador SHALL executar somente `alanocut`, sem argumentos, no diretório com a mídia bruta.
- A TUI SHALL detectar a mídia, coletar o tipo do vídeo e aceitar briefing opcional.
- O runtime SHALL usar o `.venv` único da instalação global e SHALL criar uma sessão exclusiva por execução.
- Somente `timeline.xml` SHALL ser copiado como entrega para o diretório operado.
- O produto SHALL NOT inicializar workspaces nem distribuir harness, skills de desenvolvimento ou conhecimento editorial para a pasta de mídia.

## Fora de escopo

- render final de vídeo;
- legendas, overlays, motion design, color grading ou trilha;
- publicação;
- inspeção visual automática nesta fase;
- acabamento da TUI provisória antes da decisão da GUI desktop.

## Critério de promoção

A v0.6 só pode ser tratada como release fechada depois de code review, suíte automatizada, testes de instalação e regressões reais em múltiplos formatos. Contagens antigas de testes não substituem validação executada no commit candidato.
