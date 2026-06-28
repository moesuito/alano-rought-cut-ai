# Instalando o Alano Rough Cut AI

Versão atual: **v0.2.0**

Esta documentação explica como instalar o assistente de corte bruto e exportação XML e configurar o comando global `alanocut` no Windows.

---

## Pré-requisitos

1. **Git** instalado e configurado no PATH do sistema.
2. **Python 3.10 ou superior** instalado e configurado no PATH do sistema.
3. Um terminal com privilégios de usuário normais (não requer administrador).

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
2. Criará o ambiente virtual Python (`.venv`) e instalará as dependências do `pyproject.toml`.
3. Criará os executáveis no diretório `bin/`.
4. Adicionará o diretório `bin/` nas variáveis de ambiente `PATH` do usuário.

**Importante:** Após a instalação terminar, reinicie o terminal ou a sua IDE (VS Code, Cursor, etc.) para que as alterações no `PATH` sejam aplicadas.

---

## Como Atualizar

Se o `alanocut` já está instalado, rode:

```powershell
alanocut update
```

O update compara a versão local em `config.json` com a última release do GitHub, preserva `.env` e `.venv`, baixa o novo pacote e reinstala as dependências se necessário.

O comando `alanocut init` também faz uma checagem silenciosa de update antes de criar uma nova workspace.

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
├── .env                   <-- Configure sua ElevenLabs API Key aqui
├── .gitignore
├── AGENTS.md              <-- Leia este arquivo primeiro
└── SKILL.md               <-- Stub de compatibilidade
```

Além de criar a estrutura de arquivos e pastas, o comando `alanocut init` registrará e apontará a Skill de IA do Claude Code (`~/.claude/skills/video-use`) e Gemini (`~/.gemini/config/skills/video-use`) automaticamente para esta pasta atual.

Na versão v0.2.0, essa workspace já inclui o fluxo modular `AGENTS.md` + `.agents/`, validação de cortes por waveform, QC de transcrição do preview, importação de XML corrigido de volta para EDL e nomes de timeline contextuais no XML do Premiere.

### Próximos Passos:
1. Adicione a sua chave no arquivo `.env` gerado: `ELEVENLABS_API_KEY=...`.
2. Jogue seus arquivos de vídeo na pasta `raw_video/`.
3. Abra seu agente de IA (como `claude` ou `gemini`) no diretório do projeto, leia `AGENTS.md`, e dê o comando de edição: *"Corte este vídeo bruto"* ou *"Inicializar edição"*.
