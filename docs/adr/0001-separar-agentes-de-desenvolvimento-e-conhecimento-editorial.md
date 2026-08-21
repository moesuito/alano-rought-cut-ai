# ADR 0001 — Separar agentes de desenvolvimento e conhecimento editorial

- Status: aceito
- Data: 2026-08-20
- Linha: v0.6.0

## Contexto

O repositório usava `.agents/` como harness para Codex, Antigravity e outros agentes externos executarem inventário, transcrição, edição, QC e exportação. Esse desenho misturava três responsabilidades:

1. instruções do ambiente que desenvolve o Alano Cut;
2. conhecimento editorial da LLM embutida;
3. operação determinística do pipeline.

Na v0.6, inventário, transcrição, empacotamento, refinamento acústico, QC e XML são responsabilidades do produto. A LLM recebe a transcrição pronta e decide somente conteúdo, plano, ranges e revisão editorial. Ao mesmo tempo, `.agents/skills/` passou a conter o squad instalado para desenvolver o repositório. Manter o conhecimento do produto ali criaria ambiguidade de autoridade, empacotaria skills de desenvolvimento no runtime e preservaria um caminho externo que deixou de ser canônico.

## Decisão

### Fronteiras de diretório

- `.agents/skills/` pertence exclusivamente ao ambiente de desenvolvimento local.
- `agent_knowledge/` é a biblioteca editorial do runtime instalado.
- `.squad/` contém configuração e memória operacional do squad, conforme sua própria política de versionamento.
- Python continua responsável pela orquestração, validação de schemas, ferramentas seguras e estágios determinísticos.

O legado rastreado em `.agents/{archetypes,core,prompts,state,steps}` é curado e absorvido por `agent_knowledge/`; não haverá uma segunda cópia normativa.

### Operação do produto

O fluxo suportado começa quando o usuário abre um terminal na pasta de mídia e executa somente `alanocut`. A TUI detecta os brutos, solicita tipo de vídeo e briefing opcional, usa o ambiente global instalado, cria uma sessão isolada e entrega `timeline.xml`.

`alanocut init`, workspaces com harness e famílias de subcomandos para agentes externos são legado a remover. O agente editor opera dentro da CLI própria por API OpenAI-compatible; ele não é invocado como skill por Codex, Antigravity ou similares.

### Distribuição

O instalador copia `agent_knowledge/` para `%APPDATA%\alano-rought-cut-ai\agent_knowledge`. Ele nunca distribui `.agents/skills/` nem copia a biblioteca editorial para a pasta dos vídeos. Sessões e artefatos continuam isolados em `%LOCALAPPDATA%\AlanoCut`.

Enquanto o modo one-shot existir por compatibilidade, `helpers/prompts/editor_system_prompt.md` permanece no pacote. Ele é legado, não a fonte de conhecimento do agente multi-turno e será removido com o modo one-shot.

### Carregamento seletivo

`agent_knowledge/manifest.json` é a fonte de verdade do pacote. O loader valida paths relativos, presença, tipo e versão sem adicionar dependência obrigatória. Durante a migração, o motor atual de três fases conserva seus payloads JSON legados e carrega identidade/core mais o arquétipo selecionado. O executor futuro de tools/artifacts usará as definições por fase para carregar:

1. entrypoint e módulos sempre necessários;
2. tarefa, contratos e schema da fase;
3. um arquétipo selecionado, ou no máximo dois durante ambiguidade real.

Schemas JSON fecham o formato-alvo de diagnosis, cut plan, EDL e review. Eles estão preparados e validados nesta decisão, mas só se tornam o contrato executável quando a próxima etapa conectar tools/artifacts. Nesse executor, artefatos serão versionados e o host publicará `edl.json` somente após revisão aprovada.

### Ferramentas do agente

O runtime expõe apenas `read_file`, `read_artifact` e `write_artifact`. Não expõe shell, rede ou filesystem genérico. `read_file` fica limitado a conhecimento autorizado e inputs imutáveis; artefatos da sessão são lidos exclusivamente por `read_artifact`. Writes ficam limitados a nomes allowlisted, validados por schema e gravados atomicamente. Path traversal, symlink escape, overwrite concorrente, segredos e acesso entre sessões são bloqueados.

## Consequências

Benefícios:

- separa com clareza quem desenvolve o agente de quem edita o vídeo;
- reduz contexto e permite modelos locais menores por carregamento por fase;
- mantém conhecimento editorial editável fora do Python;
- elimina instruções redundantes para tarefas já automatizadas;
- torna ferramentas e artefatos auditáveis e fail-closed.

Custos e migração:

- loader, instalador, documentação e testes precisam apontar para `agent_knowledge/`;
- o loop atual de três prompts precisará adotar artifacts e schemas gradualmente;
- workspaces antigos inicializados pelo harness não são a interface suportada;
- o modo one-shot permanece como dívida temporária, isolado do novo manifesto.

## Alternativas rejeitadas

### Manter todo o conteúdo em `.agents/`

Rejeitada porque confunde runtime e desenvolvimento e torna fácil distribuir skills locais no produto.

### Embutir conhecimento no Python

Rejeitada porque aumenta acoplamento, dificulta revisão editorial e impede carregamento seletivo.

### Copiar knowledge para cada workspace de vídeo

Rejeitada porque cria versões divergentes, aumenta exposição e reintroduz o modelo de harness externo.

### Dar shell e filesystem completo à LLM

Rejeitada pelo risco desnecessário. A tarefa editorial precisa apenas de leitura limitada e escrita de artefatos estruturados.
