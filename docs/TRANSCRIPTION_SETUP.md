# Configuração de transcrição

O Alano Cut v0.4.0 usa um perfil explícito de transcrição por workspace. O
instalador e `alanocut configure` abrem o mesmo assistente interativo; ele
mostra opções com caixas `[ ]`, guarda preferências sem segredos e nunca troca
de provider silenciosamente.

## Perfis disponíveis

| Perfil | O que usa | Credencial | Observação |
| --- | --- | --- | --- |
| **Whisper Local + Diarização (Recomendado)** | `whisper.cpp` (Vulkan) + Pyannote ONNX (DirectML) | Nenhuma | Aceleração completa em **qualquer GPU moderna** (NVIDIA, AMD Radeon, Intel Arc/Iris) com identificação de múltiplos locutores offline. |
| **Whisper Local sem Diarização** | `whisper.cpp` (Vulkan) com timestamps por palavra | Nenhuma | Modo ultrarrápido para locutor único (aulas, tutoriais, talking-head). |
| **AssemblyAI (Cloud)** | Modelo Best com timestamps por palavra e diarização | `ASSEMBLYAI_API_KEY` | Consome a conta AssemblyAI via API; suporte a diarização e pontuação automática. |
| **ElevenLabs Scribe (Cloud)** | Scribe com timestamps por palavra e diarização do provider | `ELEVENLABS_API_KEY` | Consome a conta ElevenLabs; gera transcript canônico auditável. |

O perfil local baixa aproximadamente **1.7 GiB** no total (binário leve `whisper-cli.exe` + modelo `ggml-large-v3-turbo.bin` de 1.55 GB + modelos ONNX Pyannote de ~200 MB). Os modelos ficam em cache compartilhado por usuário (`%LOCALAPPDATA%\AlanoCut\`), portanto novas workspaces não repetem o download.

## Instalação e configuração

Na primeira instalação, o assistente configura o provider recomendado. Para trocar ou verificar:

```powershell
alanocut configure
alanocut doctor
```

Para automação não interativa:

```powershell
.\install.ps1 -Provider whisper-vulkan -Diarization community-1 -NonInteractive
.\install.ps1 -Provider assemblyai -NonInteractive
.\install.ps1 -Provider elevenlabs -NonInteractive
```

## Workspace

`alanocut init` pergunta o provider ao inicializar uma nova pasta de trabalho. A credencial e preferências permanecem centralizadas:

- `%APPDATA%\alano-rought-cut-ai\user-settings.json` — preferência do usuário sem segredos;
- `%APPDATA%\alano-rought-cut-ai\.env` — API keys (`ASSEMBLYAI_API_KEY`, `ELEVENLABS_API_KEY`, etc.);
- `<workspace>\alanocut.json` — provider, idioma, device e modo de diarização.

Cada transcript canônico v2 é auditável e vinculado ao hash da fonte e à configuração exata do modelo utilizado.
