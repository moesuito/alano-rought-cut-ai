# Instalando o Alano Rough Cut AI

Versão atual: **v0.4.0**

Esta documentação explica como instalar o assistente de corte bruto e exportação XML e configurar o comando global `alanocut` no Windows.

---

## Pré-requisitos

1. **Git** instalado e configurado no PATH do sistema.
2. **Python 3.10 ou superior** instalado e configurado no PATH do sistema.
3. Um terminal com privilégios de usuário normais (não requer administrador).
4. GPU NVIDIA compatível com CUDA (a configuração normativa é uma RTX 3060 de 6 GB ou superior) e driver atualizado (opcional, para WhisperX local).
5. FFmpeg disponível no PATH.
6. Para o perfil local com speakers: acesso aceito ao `pyannote/speaker-diarization-community-1` e um Hugging Face token de leitura. Isso é opcional: WhisperX sem diarização e ElevenLabs não pedem esse token.

---

## Como Instalar (PowerShell)

Para instalar o assistente automaticamente no diretório `%APPDATA%\alano-rought-cut-ai` e configurar as variáveis de ambiente, abra o PowerShell e execute o comando abaixo (garantindo que você tenha acesso ao repositório privado):

### Opção 1: Via Download Direto do Repositório (Mais simples se já autenticado)

```powershell
irm https://raw.githubusercontent.com/moesuito/alano-rought-cut-ai/main/install.ps1 | iex
```

*Nota: Se o terminal reclamar de política de execução, você pode habilitar temporariamente rodando `Set-ExecutionPolicy Bypass -Scope Process` antes de rodar o instalador.*

### Opção 2: Execução Local (Caso tenha clonado manualmente)

Se você já clonou este repositório para a sua máquina, navegue até a pasta e execute o script localmente:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

O instalador fará o seguinte:
1. Baixará a release mais recente do GitHub para `%APPDATA%\alano-rought-cut-ai` ou, se não houver release disponível, fará fallback para clone da branch `main`.
2. Criará o ambiente virtual Python compartilhado (`%APPDATA%\alano-rought-cut-ai\.venv`), instalará as dependências (`torch`, `deepfilternet`, `pillow`, `numpy`, `requests`) e validará o modelo neural DeepFilterNet 3.
3. Abrirá um setup guiado para escolher ElevenLabs Scribe ou WhisperX local CUDA. ElevenLabs pede somente sua API key. WhisperX informa o uso aproximado de 16 GiB e permite escolher Community-1 ou sem diarização.
4. Quando o perfil local for escolhido, criará uma única vez o runtime compartilhado em `%LOCALAPPDATA%\AlanoCut\runtimes\`, com Python 3.12 e PyTorch CUDA 12.8, e baixará os modelos para o cache compartilhado.
5. Guardará preferência sem segredos em `%APPDATA%\alano-rought-cut-ai\user-settings.json` e credenciais somente em `%APPDATA%\alano-rought-cut-ai\.env`.
6. Criará os executáveis no diretório `bin/` e adicionará o diretório ao PATH.
7. No `alanocut init`, as workspaces compartilham instantaneamente o mesmo `.venv` via junction, sem duplicação de pacotes e inicializando em menos de 1 segundo.

Consulte [TRANSCRIPTION_SETUP.md](docs/TRANSCRIPTION_SETUP.md) para os fluxos dos providers, links do Community-1 e execução não interativa.

**Importante:** Após a instalação terminar, reinicie o terminal ou a sua IDE (VS Code, Cursor, etc.) para que as alterações no `PATH` sejam aplicadas.

---

## Como Atualizar

Se o `alanocut` já está instalado, rode:

```powershell
alanocut update
```

O update compara a versão local em `config.json` com a última release do GitHub, preserva `.env`, `user-settings.json` e `.venv`, baixa o novo pacote e reinstala as dependências se necessário.

O comando `alanocut init` também faz uma checagem silenciosa de update antes de criar uma nova workspace.

Para instalar/reparar ou diagnosticar apenas a transcrição local:

```powershell
alanocut setup-transcription
alanocut transcription-doctor
```

O `alanocut update` atualiza a instalação global em `%APPDATA%\alano-rought-cut-ai`. Workspaces existentes mantêm as cópias de `AGENTS.md`, `.agents/`, helpers e configuração que já estavam nelas. Depois do update, entre em cada workspace que deve receber o harness novo e rode:

```powershell
alanocut init
```

Esse refresh preserva `.env`, `raw_video/` e todo o conteúdo de `raw_video/edit/` da workspace.

---

## Como Utilizar (`alanocut init`)

Uma vez instalado e configurado no `PATH`, você não precisa copiar manualmente nenhum script ou skill de IA quando for começar a trabalhar em um novo vídeo.

Basta abrir o terminal na pasta onde estão seus arquivos brutos de vídeo e rodar:

```powershell
alanocut init
```

Esse comando inicializará a workspace configurando a seguinte estrutura:

```
<pasta_do_seu_projeto>/
├── raw_video/             <-- Jogue seus arquivos brutos aqui
│   └── edit/
├── helpers/               <-- Scripts auxiliares para corte
├── .agents/               <-- Instruções modulares por etapa
├── .venv/                 <-- Link para o runtime Python compartilhado (Instantâneo)
├── alanocut.json          <-- Configuração da workspace
├── AGENTS.md              <-- Instruções mestras para a IA
└── SKILL.md
```

Agora basta abrir o seu assistente de IA (Claude Code, Antigravity, ChatGPT, Codex, etc.) e pedir para realizar o corte!
