# Configuração de transcrição

O Alano Cut v0.4.0 usa um perfil explícito de transcrição por workspace. O
instalador e `alanocut configure` abrem o mesmo assistente interativo; ele
mostra opções com caixas `[ ]`, guarda preferências sem segredos e nunca troca
de provider silenciosamente.

## Perfis disponíveis

| Perfil | O que usa | Credencial | Observação |
| --- | --- | --- | --- |
| WhisperX local (recomendado) | `faster-whisper large-v3`, alinhamento WhisperX e CUDA | nenhuma; `HF_TOKEN` só com diarização | Sem consumo de API e sem fallback em CPU. |
| WhisperX local + Community-1 | Perfil local acima + diarização Pyannote | `HF_TOKEN` com o gate aceito | Recomendado para tomadas com mais de uma pessoa. |
| WhisperX local sem diarização | Perfil local + Silero VAD pinado | nenhuma | Mantém CUDA e alinhamento por palavra, mas não produz speakers; pode reduzir a precisão editorial em conversas. |
| Whisper Large Vulkan + Diarização (Local) | `whisper.cpp` (Vulkan) + Pyannote ONNX (DirectML GPU) | nenhuma / `HF_TOKEN` opcional | Aceleração completa em GPUs AMD Radeon, Intel Arc/Iris, etc. com identificação de múltiplos locutores offline. |
| Whisper Large Vulkan sem diarização | `whisper.cpp` (Vulkan) com timestamps por palavra | nenhuma | Modo ultrarrápido para locutor único em GPUs não-NVIDIA. |
| ElevenLabs Scribe (Cloud) | Scribe com timestamps por palavra e diarização do provider | `ELEVENLABS_API_KEY` | Consome a conta ElevenLabs; também gera transcript canônico auditável. |
| AssemblyAI (Cloud) | Modelo Best com timestamps por palavra e diarização | `ASSEMBLYAI_API_KEY` | Consome a conta AssemblyAI via API; suporte a diarização e pontuação. |

O perfil local baixa aproximadamente **16 GiB** entre runtime e modelos; o
assistente pede pelo menos 12 GiB livres e recomenda 18 GiB. Os modelos ficam
em cache compartilhado por usuário, portanto workspaces novos não repetem o
download. A RTX 3060 é suportada pelo perfil CUDA; CPU não é uma alternativa
aceita.

O modo sem diarização não baixa nem executa o modelo Community-1 e não pede
token Hugging Face. O pacote Pyannote continua sendo uma dependência interna do
WhisperX instalado no runtime, mas não é usado para inferência nesse perfil.

Para Community-1, abra a página do modelo,
[aceite o gate](https://huggingface.co/pyannote/speaker-diarization-community-1)
e crie um token Read ou fine-grained na
[página de tokens do Hugging Face](https://huggingface.co/settings/tokens).
O token precisa pertencer à conta que aceitou o gate. O assistente valida o
acesso antes de iniciar o download e o campo de senha não é exibido no terminal.

## Instalação e configuração

Na primeira instalação, o assistente pergunta pelo provider. Para trocar ou
reparar depois:

```powershell
alanocut configure
alanocut setup-transcription
alanocut transcription-doctor
```

`setup-transcription` reaplica o perfil já escolhido. Para automação sem
perguntas, informe o perfil explicitamente e injete somente a credencial
necessária no ambiente:

```powershell
.\install.ps1 -Provider elevenlabs -NonInteractive
.\install.ps1 -Provider whisperx -Diarization none -NonInteractive
```

Com `-Provider whisperx -Diarization community-1 -NonInteractive`, `HF_TOKEN`
precisa estar no ambiente antes da execução. Com ElevenLabs,
`ELEVENLABS_API_KEY` é obrigatória. Não passe nenhum segredo como argumento.

## Workspace

`alanocut init` pergunta o provider **antes** de copiar helpers, instruções ou
criar pastas. Ao terminar, ele grava somente o perfil escolhido em
`<workspace>/alanocut.json`. A credencial permanece global:

- `%APPDATA%\alano-rought-cut-ai\user-settings.json` — preferência global sem segredos;
- `%APPDATA%\alano-rought-cut-ai\.env` — `HF_TOKEN` e/ou `ELEVENLABS_API_KEY`;
- `<workspace>\alanocut.json` — provider, idioma, CUDA/cloud e modo de diarização.

Assim, uma workspace pode usar ElevenLabs e outra WhisperX sem copiar API keys
para o projeto, para transcripts ou para Git. `init` aceita flags quando for
necessário automatizar a escolha:

```powershell
alanocut init --provider whisperx --diarization none --non-interactive
alanocut init --provider elevenlabs --non-interactive
```

Cada transcript canônico v2 é ligado ao provider, à configuração e ao hash da
fonte. O preview é sempre transcrito novamente pelo mesmo provider e pela
configuração exata dos transcripts-fonte selecionados pela EDL. Um cache de
provider diferente é considerado stale, e não existe fallback automático entre
ElevenLabs e WhisperX.
