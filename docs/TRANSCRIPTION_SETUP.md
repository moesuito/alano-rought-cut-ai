# Transcrição local v0.6.0

## Caminho canônico

A v0.6 usa uma stack local Windows:

| Etapa | Componente | Aceleração |
| --- | --- | --- |
| Denoising | DeepFilterNet 3 | runtime local |
| ASR | Whisper.cpp Large v3 Turbo | Vulkan |
| Word alignment | Wav2Vec2 CTC | DirectML |
| Speaker diarization | Pyannote ONNX | DirectML |

Vulkan e DirectML permitem um caminho cross-vendor para AMD, NVIDIA e Intel. O objetivo é manter um contrato único de transcrição, independentemente do fabricante da GPU.

WhisperX, ElevenLabs e AssemblyAI não são provedores canônicos v0.6. Módulos antigos ainda podem aparecer no repositório por compatibilidade histórica; não os selecione em novos testes e não adicione novas dependências a esses caminhos antes do code review de remoção.

## Instalação

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

A instalação global fica em:

```text
%APPDATA%\alano-rought-cut-ai
```

Modelos, caches e sessões ficam em:

```text
%LOCALAPPDATA%\AlanoCut
```

## Execução

Abra um terminal na pasta que contém os vídeos e execute:

```powershell
alanocut
```

A TUI detecta a mídia no diretório atual e conduz a execução. `alanocut` sem argumentos é a única interface pública; seleção de provider, idioma, setup e diagnóstico permanecem detalhes internos do runtime, não subcomandos do usuário.

## Contrato da transcrição

Cada fonte editorialmente relevante deve produzir JSON canônico com:

- hash da mídia original;
- versão do schema;
- identificação dos modelos/runtimes;
- palavras com `start < end`;
- cobertura temporal completa para as palavras usadas no EDL;
- speaker IDs quando a diarização estiver habilitada;
- proveniência suficiente para invalidar caches antigos ou incompatíveis.

O pipeline não deve inventar timestamps ausentes nem aceitar silenciosamente uma transcrição segment-only para cortes palavra a palavra.

## Responsabilidades dos componentes

- DeepFilterNet fornece áudio mais estável para ASR/análise, sem alterar a mídia original.
- Whisper produz o texto inicial e timestamps do provider local.
- Wav2Vec2 refina o alinhamento temporal das palavras.
- Pyannote separa speakers.
- `transcription_contract.py` normaliza e valida a evidência.
- `pack_transcripts.py` gera a leitura condensada que será entregue ao agente editorial.

## Direção de produto

A transcrição deve permanecer 100% local. APIs remotas de ASR não fazem parte do roadmap v0.6. A única integração remota ainda tolerada durante o desenvolvimento é a LLM editorial OpenAI-compatible, que também será validada com modelos locais.
