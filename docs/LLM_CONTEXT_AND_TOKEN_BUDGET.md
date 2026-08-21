# Contexto, tokens e benchmark de LLM

## Decisão provisória

O GLM-5.2 via NVIDIA NIM será o baseline de qualidade durante a construção do runtime. A primeira hipótese para operação local é uma janela de **128K** com um modelo de **14B** parâmetros. Isso é uma hipótese a medir, não uma conclusão sobre suficiência.

O objetivo não é encontrar o menor modelo que ocasionalmente produz uma boa edição. É encontrar o menor modelo que, de forma previsível:

- segue as tools e os schemas;
- compreende português e intenção editorial;
- resolve retakes e continuidade sem inventar evidência;
- revisa o próprio plano em loops curtos;
- falha de forma explícita quando não consegue concluir.

A baseline é configurada somente no `.env` da instalação confiável ou no ambiente do processo. Arquivos da pasta de mídia não podem trocar endpoint ou modelo. O contrato aceita NIM remoto por HTTPS e servidores locais OpenAI-compatible por HTTP em loopback.

## Fatos confirmados nas fontes oficiais

- A página do [GLM-5.2 no NVIDIA NIM](https://build.nvidia.com/z-ai/glm-5.2/modelcard) informa janela de input de 1.000.000 tokens, conversas multi-turno, tool calling e output estruturado. O exemplo oficial usa o endpoint OpenAI-compatible `https://integrate.api.nvidia.com/v1` e o identificador `z-ai/glm-5.2` na [página de integração](https://build.nvidia.com/z-ai/glm-5.2). O milhão de tokens é capacidade anunciada, não evidência de que o AlanoCut precise dela.
- O [Qwen3](https://qwenlm.github.io/blog/qwen3/) oferece variantes densas 8B e 14B e a variante MoE 30B-A3B com janela de 128K; a publicação também descreve suporte a português, capacidades agênticas e opções locais como Ollama, LM Studio, llama.cpp e vLLM.
- A documentação da Mistral informa 256K para a família [Ministral 3 de 3B, 8B e 14B](https://docs.mistral.ai/resources/known-limitations). O modelo [Ministral 3 14B](https://docs.mistral.ai/models/ministral-3-14b-25-12) declara Chat Completions, structured outputs e function calling.
- O model card oficial do [Llama 3.1](https://github.com/meta-llama/llama-models/blob/main/models/llama3_1/MODEL_CARD.md) lista a variante 8B com 128K e português entre os idiomas suportados. A [publicação da Meta](https://ai.meta.com/blog/meta-llama-3-1/) também descreve 128K e tool use para as variantes atualizadas 8B e 70B.

Essas especificações demonstram capacidade nominal. Elas não medem qualidade editorial, aderência aos schemas do projeto, desempenho em português real nem velocidade na máquina-alvo.

## Hipóteses de tamanho

| Faixa | Papel no benchmark | Hipótese, não garantia |
|---|---|---|
| 8B | piso experimental | pode atender fases estreitas; maior risco de tool/schema drift e revisão editorial superficial |
| 14B | primeiro alvo local | melhor equilíbrio inicial entre memória, latência e previsibilidade |
| 24B–30B | casos difíceis | fallback para VSL longa, muitos retakes, estrutura fragmentada ou falhas persistentes no 14B |
| GLM-5.2/NIM | baseline remoto | referência de qualidade e telemetria, não ground truth editorial |

Qwen3 8B/14B/30B-A3B, Ministral 3 8B/14B e Llama 3.1 8B entram como candidatos técnicos. A seleção final depende de execução no mesmo harness, no mesmo idioma e com as mesmas tools; a tabela não é um ranking de qualidade.

## Por que 128K é uma hipótese razoável

O agente não precisa manter toda a sessão como chat. Conhecimento modular, artefatos compactos e chamadas reconstruídas por fase removem histórico redundante. Os maiores consumidores esperados passam a ser:

1. transcrição ou slices de evidência;
2. instruções e contrato da fase;
3. módulos editoriais selecionados;
4. artefatos predecessores;
5. schemas e descrições de tools;
6. output reservado para a fase.

Descrições de tools também consomem contexto; a [documentação da Mistral](https://docs.mistral.ai/resources/known-limitations) confirma essa contabilização e recomenda function calling quando aderência estrutural importa. Por isso o MVP limita-se a três tools com contratos curtos.

Sem medições reais, não se deve converter minutos de vídeo em tokens por uma regra fixa: tokenização varia por modelo, idioma, timestamps, JSON e formatação. A janela de 128K só será considerada confortável se o footprint observado no benchmark permanecer com folga.

## Gate de ocupação da janela

Para cada chamada:

```text
footprint_tokens = prompt_tokens + completion_tokens
context_ratio = footprint_tokens / context_window_tokens
```

Gate inicial sugerido para o conjunto representativo:

- `p95(context_ratio) <= 60%–70%`;
- nenhuma chamada truncada ou rejeitada por contexto;
- output máximo reservado sem ultrapassar a janela;
- nenhuma queda mensurável de qualidade causada por compactação.

Em uma janela de 128K, 60%–70% corresponde a aproximadamente 76,8K–89,6K tokens observados. A reserva de 30%–40% absorve variações de tokenizer, crescimento de uma crítica, tool results e casos mais longos. Esse percentual é um gate de engenharia do projeto, não uma recomendação do fornecedor.

Se o p95 ultrapassar o gate, a ordem de ação é:

1. remover histórico redundante e módulos não usados;
2. reduzir schemas e descrições de tools sem perder validação;
3. adotar `read_transcript_slice` com evidência rastreável;
4. dividir análise e planejamento em unidades coerentes;
5. somente então testar uma janela maior, por exemplo 160K ou 256K.

## Telemetria implementada

Cada completion gera uma linha sanitizada em `edit/agent/llm_usage.jsonl`:

| Grupo | Campos atuais |
|---|---|
| Identidade | timestamp UTC, `run_id`, `call_id`, `phase`, `iteration` |
| Modelo | ID configurado para a execução |
| Tokens | `prompt_tokens`, `completion_tokens`, `total_tokens` quando informados e `source` normalizada |
| Execução | latência e `finish_reason` sanitizado |
| Tools | nomes das tools chamadas, nunca argumentos ou resultados |
| Qualidade | status de validação, outcome e código estável de erro |

Não registrar:

- prompt, brief ou transcrição em texto cru;
- conteúdo de tool call/result ou resposta bruta;
- API key, headers, query strings ou `.env`;
- raciocínio oculto do provedor;
- caminho físico completo quando um nome lógico basta.

`edit/agent/agent_run.json` agrega estado, fase, iterações, quantidade de chamadas, totais de prompt/completion, último erro e nomes/revisões/hashes dos artefatos. O ledger não calcula ainda p50/p95, ocupação da janela, preço ou retries. Essas métricas devem ser derivadas do corpus com uma janela configurada e, no caso de preço, uma tabela versionada; nunca inferidas apenas do model ID.

## `usage` do provedor e estimativa

Quando a resposta OpenAI-compatible contém `usage`, o cliente estruturado preserva os campos inteiros validados e a telemetria normaliza `prompt_tokens`, `completion_tokens` e `total_tokens`. A referência da OpenAI mostra esses campos no [objeto de Chat Completions](https://platform.openai.com/docs/api-reference/chat/object). Backends compatíveis podem omitir campos ou tokenizar de forma diferente.

Se `usage` não vier, as contagens ficam ausentes. Ainda não existe estimador por tokenizer; portanto, uma chamada sem contagem não pode participar de percentis como se fosse zero. Uma futura estimativa deverá identificar tokenizer/versão e nunca se misturar à contagem do provedor sem distinção.

O cliente atual é não streaming. Seu envelope estruturado já retorna conteúdo, tool calls, `usage`, modelo efetivo, request ID seguro, `finish_reason` e latência. Streaming permanece fora do runtime ativo; quando for avaliado, `include_usage` e interrupção precisam de contrato específico.

## Crescimento do histórico

Reenviar a conversa inteira faz o input crescer a cada loop e cobra novamente por instruções, respostas e tool results anteriores. Além do custo, isso aumenta distração e reduz a previsibilidade de modelos menores.

O runtime artifact-driven implementa:

- iniciar cada fase a partir de um envelope reconstruído;
- persistir a decisão válida antes de descartar mensagens intermediárias;
- carregar artefatos por referência e conteúdo somente quando necessários;
- retirar respostas inválidas e tool results duplicados do próximo prompt;
- manter hashes e eventos no estado técnico, fora do chat;
- reconstruir cada iteração de review em uma conversa nova.

Dentro de uma fase, a conversa cresce apenas com as tool calls necessárias até `write_artifact`. Não existe compactação heurística: ao persistir o artefato, a fase seguinte recomeça do estado durável.

Não se deve resumir uma transcrição e depois tratar o resumo como evidência temporal. A montagem usa palavras e timestamps canônicos, diretamente ou via slice verificável.

## Benchmark antes de adotar modelo local

### Corpus

Montar casos representativos e versionados de:

- Reels com gancho e limite curto;
- vídeo de YouTube com estrutura longa;
- videoaula com listas e cadência didática;
- VSL com argumento, prova, transições e CTA;
- material fragmentado com pickups, falsos inícios e retakes concorrentes.

O GLM-5.2 e cada candidato local recebem os mesmos inputs, conhecimento, schemas, tools, limites e número máximo de loops.

### Métricas obrigatórias

- taxa de tool calls válidas;
- taxa de outputs compatíveis com schema sem reparo;
- referências de fonte/timestamp existentes;
- cobertura de beats obrigatórios;
- acerto na resolução de retakes;
- aprovação humana cega da coerência editorial;
- variância entre execuções repetidas;
- loops, retries, latência e tokens por fase/sessão;
- p50, p95 e máximo de ocupação de contexto;
- comportamento fail-closed diante de input, tool e artifact inválidos.

### Promoção

Um modelo local só vira default se:

1. passar todos os gates estruturais e de segurança;
2. não regredir materialmente a qualidade editorial do baseline no corpus;
3. permanecer previsível em repetições;
4. atender o gate de contexto e a latência da máquina-alvo;
5. demonstrar em logs onde falhou quando não concluiu.

O resultado pode ser híbrido: 8B ou 14B para compreensão e validações estreitas, com 24B–30B reservado a planejamento global ou recuperação de casos difíceis. Essa separação também deve ser provada pelo benchmark; não será assumida apenas pelo número de parâmetros.

## Próximos passos de medição

1. Executar o GLM-5.2/NIM no corpus e registrar chamadas sem conteúdo privado.
2. Calcular por fora do runtime p50, p95, máximo e taxa de chamadas sem `usage`.
3. Associar cada run à janela configurada para medir `context_ratio` sem inferir capacidade pelo nome do modelo.
4. Fixar fixtures e rubrica humana para qualidade editorial, retakes e cobertura de beats.
5. Comparar primeiro um 14B local, depois o piso 8B e, se necessário, a faixa 24B–30B.
6. Adicionar estimativa versionada somente para provedores que omitem `usage`.
