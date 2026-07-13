# Instalando o Alano Rough Cut AI

Versão atual: **v0.4.0**

Esta documentação explica como instalar o assistente de corte bruto e exportação XML e configurar o comando global `alanocut` no Windows.

---

## Pré-requisitos

1. **Git** instalado e configurado no PATH do sistema.
2. **Python 3.10 ou superior** instalado e configurado no PATH do sistema.
3. Um terminal com privilégios de usuário normais (não requer administrador).
4. GPU NVIDIA compatível com CUDA (a configuração normativa é uma RTX 3060 de 6 GB ou superior) e driver atualizado.
5. FFmpeg disponível no PATH.
6. Acesso aceito ao modelo `pyannote/speaker-diarization-community-1` e um Hugging Face token de leitura.

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
2. Criará o ambiente virtual leve Python (`.venv`) e instalará as dependências do `pyproject.toml`.
3. Solicitará o Hugging Face token com entrada oculta, preservando outras chaves do `.env`.
4. Criará uma única vez o runtime compartilhado em `%LOCALAPPDATA%\AlanoCut\runtimes\`, com Python 3.12 e PyTorch CUDA 12.8.
5. Criará os executáveis no diretório `bin/` e adicionará o diretório ao PATH.

**Importante:** Após a instalação terminar, reinicie o terminal ou a sua IDE (VS Code, Cursor, etc.) para que as alterações no `PATH` sejam aplicadas.

---

## Como Atualizar

Se o `alanocut` já está instalado, rode:

```powershell
alanocut update
```

O update compara a versão local em `config.json` com a última release do GitHub, preserva `.env` e `.venv`, baixa o novo pacote e reinstala as dependências se necessário.

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
├── .env                   <-- Configure HF_TOKEN para Community-1
├── .gitignore
├── AGENTS.md              <-- Leia este arquivo primeiro
└── SKILL.md               <-- Stub de compatibilidade
```

Além de criar a estrutura de arquivos e pastas, o comando `alanocut init` registrará e apontará a Skill de IA do Claude Code (`~/.claude/skills/video-use`) e Gemini (`~/.gemini/config/skills/video-use`) automaticamente para esta pasta atual.

Na versão v0.4.0, o fluxo do agente é estritamente audio-only: sem fades, inspeção de frames ou renderização visual. Depois da decisão editorial, a ordem obrigatória é refinamento de limites -> `preview.wav`/mapa -> QC de áudio -> QC semântico -> transcrição forçada e persistida do preview com hash -> QC de transcript por join -> `verify_edit_ready.py` com exit code 0 -> XML. `alanocut init` instala os helpers, o modelo RNNoise e as instruções dessa cadeia; `timeline_view.py` permanece apenas como diagnóstico manual legado até sua remoção na v0.5.0.

### Próximos Passos:
1. Adicione `HF_TOKEN=...` ao `.env` gerado caso não o tenha informado no instalador. Nunca passe o token como argumento de linha de comando.
2. Jogue seus arquivos de vídeo na pasta `raw_video/`.
3. Abra seu agente de IA (como `claude` ou `gemini`) no diretório do projeto, leia `AGENTS.md`, e dê o comando de edição: *"Corte este vídeo bruto"* ou *"Inicializar edição"*.

O EDL criado pelo agente deve declarar `metadata.sequence_fps`, `metadata.required_beats` (que pode ser uma lista vazia) e `ranges[].beat_id`. Em `evidence_any_of`, cada lista interna representa um conjunto alternativo de frases que deve aparecer nas palavras realmente selecionadas.
