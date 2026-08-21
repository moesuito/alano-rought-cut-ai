# Instalação do Alano Rough Cut AI

Versão atual: **v0.6.0**.

O produto usa uma instalação global no Windows. Não existe inicialização de workspace: a pasta de vídeos permanece somente como entrada e destino de `timeline.xml`.

## Pré-requisitos

- Windows 10 ou 11 com PowerShell;
- Python 3.10 ou superior no `PATH`;
- FFmpeg e drivers de GPU compatíveis com a stack local descrita em [`docs/TRANSCRIPTION_SETUP.md`](docs/TRANSCRIPTION_SETUP.md).

## Instalar

Em um checkout do repositório:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

O instalador:

1. usa o próprio checkout que contém `install.ps1` como fonte;
2. sincroniza somente a árvore de runtime permitida para `%APPDATA%\alano-rought-cut-ai`;
3. preserva `.env`, `.venv` e `user-settings.json` em reinstalações;
4. cria um único `.venv` compartilhado na instalação global;
5. instala as dependências Python e valida o denoiser local;
6. instala `agent_knowledge/`, `helpers/` e o launcher `bin/alanocut.ps1`;
7. adiciona o diretório `bin` ao `PATH` do usuário.

`.agents/` e `.squad/` são estruturas de desenvolvimento e não fazem parte do runtime instalado. `agent_knowledge/` é a biblioteca editorial do produto; ela fica na instalação global e não é copiada para a pasta dos vídeos.

O instalador não consulta releases, não clona branches e não troca silenciosamente a fonte do runtime. Para instalar outra revisão, abra explicitamente o checkout desejado antes de executar `install.ps1`.

Depois da primeira instalação, abra um novo terminal para que a alteração do `PATH` seja reconhecida.

## Atualizar ou reparar

Execute novamente o instalador a partir de um checkout atualizado:

```powershell
git pull --ff-only
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Essa reinstalação atualiza a árvore de runtime e mantém os três itens persistentes: `.env`, `.venv` e `user-settings.json`. Não há comando público separado de atualização, configuração ou diagnóstico.

## Executar

Abra o terminal diretamente na pasta que contém os arquivos brutos e rode somente:

```powershell
alanocut
```

A TUI:

1. detecta a mídia no diretório atual;
2. solicita o tipo de vídeo;
3. aceita um briefing opcional;
4. cria uma sessão isolada em `%LOCALAPPDATA%\AlanoCut\sessions`;
5. automatiza transcrição, agente editorial, refinamento, QC e exportação;
6. copia `timeline.xml` para a pasta em que o comando foi executado.

`alanocut` não aceita subcomandos nem argumentos. Rotinas internas de configuração ou diagnóstico podem existir como módulos Python, mas não são UX pública.

## Diretórios persistentes

```text
%APPDATA%\alano-rought-cut-ai
  .env
  .venv\
  user-settings.json
  agent_knowledge\
  bin\
  helpers\

%LOCALAPPDATA%\AlanoCut
  sessions\
  models\
  cache\
```

A pasta de mídia não recebe cópias de código, skills, conhecimento editorial ou ambiente virtual.

## LLM editorial

O cliente usa um endpoint OpenAI-compatible. A configuração pode ser mantida em `%APPDATA%\alano-rought-cut-ai\.env`:

```dotenv
LLM_API_KEY=
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=z-ai/glm-5.2
```

Não coloque segredos na pasta dos vídeos, em argumentos de linha de comando ou em arquivos rastreados pelo Git.
