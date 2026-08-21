# Contexto, tokens e benchmark de LLM

## Decisão provisória

O GLM-5.2 via NVIDIA NIM será o baseline de qualidade durante a construção do runtime. A primeira hipótese para operação local é uma janela de **128K** com um modelo de **14B** parâmetros. Isso é uma hipótese a medir, não uma conclusão sobre suficiência.

O objetivo não é encontrar o menor modelo que ocasionalmente produz uma boa edição. É encontrar o menor modelo que, de forma previsível:

- segue as tools e os schemas;
- compreende português e intenção editorial;
- resolve retakes e continuidade sem inventar evidência;
- revisa o próprio plano em loops curtos;
- falha de forma explícita quando não consegue concluir.

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

## Telemetria por chamada

Uma linha de `llm_usage.jsonl` deve conter, no mínimo:

| Grupo | Campos |
|---|---|
| Identidade | `run_id`, `call_id`, `phase`, `iteration`, timestamp UTC |
| Modelo | provider lógico, model ID retornado, janela configurada, modo de reasoning quando exposto |
| Tokens | input/prompt, output/completion, total, cached e reasoning quando disponíveis |
| Proveniência | `usage_source: provider` ou `usage_source: estimate`, tokenizer/estimador e flag de completude |
| Execução | latência, retries, HTTP status, request ID seguro, `finish_reason`, streaming |
| Tools | quantidade, nomes, erros e bytes lidos/escritos; nunca conteúdo |
| Qualidade | parse, schema, evidence gate, estado final da fase e motivo de bloqueio |
| Artefatos | nomes lógicos, versões, hashes e tamanhos |

Não registrar:

- prompt, brief ou transcrição em texto cru;
- conteúdo de tool call/result ou resposta bruta;
- API key, headers, query strings ou `.env`;
- raciocínio oculto do provedor;
- caminho físico completo quando um nome lógico basta.

O log de sessão agrega chamadas em `agent_run.json`: tokens totais, máximo e p95 de ocupação, latência, loops, retries, erros de schema/tool e resultado editorial. Preço pode ser calculado somente com uma tabela de preço versionada e identificada; não deve ser inferido do model ID.

## `usage` do provedor e estimativa

Quando a resposta OpenAI-compatible contiver `usage`, o runtime preserva os campos do provedor e os normaliza sem recontar. A referência da OpenAI mostra `prompt_tokens`, `completion_tokens` e `total_tokens` no [objeto de Chat Completions](https://platform.openai.com/docs/api-reference/chat/object). Backends compatíveis podem omitir campos ou tokenizar de forma diferente; por isso o payload bruto de `usage` pode ser guardado como metadado estruturado não sensível.

Se `usage` não vier:

- usar o tokenizer exato do modelo quando disponível;
- registrar o resultado como `usage_source: estimate` e identificar versão do tokenizer;
- nunca misturar estimativa e contagem do provedor sem distinção;
- marcar a chamada como `usage_complete: false` quando output ou reasoning não puderem ser estimados.

No streaming, solicitar `stream_options.include_usage` apenas quando o backend declarar suporte. As referências de [Chat Completions da OpenAI](https://platform.openai.com/docs/api-reference/chat/object) e de [limitações da Mistral](https://docs.mistral.ai/resources/known-limitations) observam que essa opção precisa ser explícita e que uma interrupção pode impedir a chegada do evento final de uso. Nesse caso, a telemetria usa estimativa e registra interrupção; não inventa uma contagem exata.

O cliente atual é não streaming, mas descarta `usage`. A implementação futura deve retornar um envelope com conteúdo, uso, modelo efetivo, request ID, `finish_reason` e latência.

## Crescimento do histórico

Reenviar a conversa inteira faz o input crescer a cada loop e cobra novamente por instruções, respostas e tool results anteriores. Além do custo, isso aumenta distração e reduz a previsibilidade de modelos menores.

O runtime artifact-driven deve:

- iniciar cada fase a partir de um envelope reconstruído;
- persistir a decisão válida antes de descartar mensagens intermediárias;
- carregar artefatos por referência e conteúdo somente quando necessários;
- retirar respostas inválidas e tool results duplicados do próximo prompt;
- manter hashes e eventos no estado técnico, fora do chat;
- compactar somente com regra determinística e rastreável.

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

## Próximos passos antes do primeiro teste real

1. Fazer o cliente preservar `usage` e metadados da resposta.
2. Criar `llm_usage.jsonl` e agregação em `agent_run.json`.
3. Implementar envelopes de fase e descarte seguro do histórico.
4. Fixar schemas e fixtures de benchmark.
5. Executar o GLM-5.2 como baseline.
6. Comparar primeiro um 14B, depois o piso 8B e, se necessário, a faixa 24B–30B.
