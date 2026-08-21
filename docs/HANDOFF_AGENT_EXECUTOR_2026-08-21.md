# Handoff — executor agêntico por artefatos

Data: 2026-08-21
Branch: `codex/feature-agent-artifact-executor`
Base anterior: `390776e`

## Estado entregue

- O fluxo público continua sendo somente `alanocut`, sem argumentos, no diretório dos vídeos.
- O motor editorial padrão foi substituído por `diagnose -> plan -> assemble -> review`.
- As tools disponíveis ao modelo são `read_file`, `read_artifact` e `write_artifact`, com enums e allowlists exatas por fase.
- Inputs e artefatos são imutáveis/versionados; revisão, revisions, hashes e publicação são controlados pelo host.
- A EDL editorial aprovada fica em `edit/agent/artifacts/edl.json`; `edit/edl.json` é uma projeção técnica separada e reidratada pelo host.
- Paths físicos ficam no registry privado da sessão e não entram no prompt, nos artefatos editoriais ou na telemetria.
- Falha do agente, refiner/QC em review, relatório ausente ou readiness diferente de pass bloqueiam XML.
- Somente quatro QCs válidos mais readiness pass permitem a publicação atômica de `<videos_dir>/timeline.xml`.
- O cliente LLM lê configuração somente da instalação confiável, bloqueia redirects e endpoints inseguros e limita a resposta HTTP.
- Erros transitórios `408`, `429`, `500`, `502`, `503`, `504` e falhas de transporte recebem no máximo dois retries por fase, com backoff de 1 s e 2 s e telemetria por tentativa.
- Falhas técnicas terminam em `failed`; incerteza editorial, `NO_PROGRESS` e limites de budget/review terminam em `needs_human_review`.
- Um lote de tool calls sempre recebe uma resposta por call ID; após a primeira falha, as calls restantes são recusadas com `BATCH_ABORTED` e nenhuma escrita posterior é executada.
- Antes do pack, o conjunto de transcripts precisa corresponder exatamente ao inventário congelado; áudio ou mídia extra não pode entrar silenciosamente no contexto da LLM.
- A telemetria registra fase, tools, status, latência e tokens, sem prompt, brief, transcrição, resposta bruta, chain-of-thought ou segredos.

## ISSUE-001

Resolvida. O blob RNNoise era o mesmo do upstream; `core.autocrlf=true` convertia seus 22 LF para CRLF no checkout e alterava o hash. `.gitattributes` agora trata `helpers/models/*.rnnn` como binário. A prova completa está em `docs/known-issues/ISSUE-001-rnnnoise-model-hash.md`.

## Evidência automatizada

- Suíte final completa em Python 3.12: `413 passed, 1 skipped` (em 28.91s).
- O skip é um teste privado opt-in já existente (`test_lesson08_regression_suite_private_opt_in`).
- Parser de `install.ps1`, `py_compile` (compileall de helpers e tests) e `git diff --check`: aprovados com zero erros.
- Catálogo lexical real: três transcripts, 458 palavras, aceitos pelo contrato canônico.
- Overlaps canônicos de palavra: até 250 ms são permitidos; o material real observado chegou a 47 ms.
- Runtime global sincronizado em `%APPDATA%\alano-rought-cut-ai` com `.env`, `.venv` e `user-settings.json` preservados via `install.ps1 -RuntimeSyncOnly`.
- Smoke offline instalado importou todos os módulos novos e compilou cinco schemas locais.

## Smokes NVIDIA NIM / GLM-5.2

### Sessão 1

`session_20260821_011925_raw_video_17b51d`

- Três fontes do `raw_video/` foram tratadas como um único projeto.
- O agente realizou duas leituras no diagnóstico.
- Em seguida tentou um recurso fora da allowlist e uma chamada do provider falhou.
- O smoke expôs uma classificação histórica incorreta como `needs_human_review` para falha técnica. O runtime atual corrige esse caso para `failed`; nenhuma EDL ou timeline foi publicada.
- Uso registrado: 3 chamadas, 12.953 tokens de prompt e 177 tokens de saída.

Esse smoke motivou enums exatos nos schemas de tools, validação antecipada de `call_id` e retries transitórios limitados.

### Probe de protocolo

Um probe mínimo confirmou que o NIM aceita o ciclo OpenAI de tool call, tool result estruturado e continuação. O call ID retornado pelo GLM era válido e o enum único foi obedecido.

### Sessão 2

`session_20260821_012630_raw_video_e14c81`

- Executada após enums exatos, retries e ressincronização do runtime.
- Primeira chamada do diagnóstico concluída: duas `read_file`, 5.514 tokens de prompt, 47 tokens de saída e 8.433 ms.
- O usuário pediu encerramento para preservar o limite semanal antes da próxima chamada.
- O ledger foi finalizado como `needs_human_review` com `OPERATOR_INTERRUPTED`.
- Nenhuma EDL ou timeline foi publicada.

### Sessão 3

`session_20260821_015644_raw_video_2ff6e9`

- Sessão isolada iniciada a partir das três fontes e com seeding dos transcripts canônicos correspondentes.
- Primeira chamada da fase `diagnose` concluída com sucesso: duas `read_file`, 5.517 tokens de prompt, 47 tokens de saída e 10.663 ms de latência.
- As chamadas subsequentes atingiram timeout de 120s da API remota (`PROVIDER_TRANSIENT`) em duas tentativas consecutivas por esgotamento de cota/rate limit na chave de API do provedor (NVIDIA NIM).
- O teste foi cancelado pelo operador para atualização de credencial.
- Nenhuma timeline ou EDL foi corrompida ou publicada (comportamento fail-closed estrito mantido).

### Sessão 4

`session_20260821_020420_raw_video_d386c8`

- Executada com nova credencial NVIDIA NIM no `.env`.
- Primeira chamada da fase `diagnose` (`diagnose-1-1-1`): respondida com sucesso em 10.859 ms (5.517 tokens de prompt, 47 de completion), emitindo duas tool calls `read_file` para `brief.md` e `takes_packed.md`.
- Segunda chamada (`diagnose-1-2-1` e `diagnose-1-2-2`): atingiu timeout de 120s do cliente HTTP (`PROVIDER_TRANSIENT`) em duas tentativas consecutivas de envio dos tool results para `https://integrate.api.nvidia.com/v1/chat/completions`.
- **Diagnóstico técnico do travamento**: O endpoint sandbox da NVIDIA NIM (`z-ai/glm-5.2`) apresenta latência/enfileiramento superior a 120 segundos para processar o contexto multi-turno contendo mensagens de tool result volumosas (`takes_packed.md`), ou o backend remoto da NVIDIA sofre de throttling/fila severa no cluster. O timeout fixado de 120s no cliente Python encerra a tentativa como `PROVIDER_TRANSIENT` e aciona o retry com backoff.
- O teste foi cancelado pelo operador. Nenhuma timeline ou EDL foi corrompida ou publicada (comportamento fail-closed estrito mantido).

### Sessão 5 (llama-server local — Ling-3.0-tiny-Q4_K_S.gguf)

`http://127.0.0.1:8080/v1`

- `_parse_usage` em `helpers/llm_client.py` foi atualizado para suportar dicionários aninhados de tokens (`*_details`) do llama.cpp / vLLM. A suíte de testes do cliente LLM confirmou 52 testes aprovados.
- Servidor local `llama-server.exe` (Vulkan) executado com o modelo `Ling-3.0-tiny-Q4_K_S.gguf` na porta 8080 (1 slot, contexto de 128K).
- O agente editorial executou o ciclo real da fase `diagnose` em 6 turnos consecutivos:
  - Turno 1 (`diagnose-1-1-1`): executado em 2.757 ms (6.304 prompt, 166 completion tokens), gerando 9 tool calls `read_file` em lote para carregar brief, takes e contratos.
  - Turnos 2, 3 e 5 (`diagnose-1-2-1`, `diagnose-1-3-1`, `diagnose-1-5-1`): o modelo gerou payloads extensos de `write_artifact` (~4.600 tokens de JSON por turno em ~90s no Vulkan), mas os outputs violaram restrições de `diagnosis.schema.json`, recebendo `SCHEMA_MISMATCH`.
  - Turnos 4 e 6: o modelo tentou reler contratos, mas ao repetir leituras idênticas sem progresso estrutural, o host abortou a fase por `NO_PROGRESS`.
- **Telemetria agregada da sessão**: 6 chamadas, 122.841 tokens de prompt acumulados, 13.973 tokens de completion gerados, latência média por token consistente com o backend Vulkan local.
- **Conclusão de arquitetura**: O runtime agêntico por artefatos comprovou suporte técnico pleno ao `llama-server` local via OpenAI API e demonstrou proteção fail-closed contra desvios de schema em modelos menores/sub-14B.
- Nenhuma timeline ou EDL foi corrompida ou publicada (comportamento fail-closed estrito mantido).

O `raw_video/timeline.xml` preexistente não foi alterado por esses smokes editoriais.

## Próxima continuação

1. Testar modelos com maior capacidade de raciocínio e aderência estrita a schemas JSON Draft 2020-12 (ex.: modelos de 14B a 32B ou modelos com grammar-constrained decoding ativado no llama.cpp como `--grammar` ou tool schemas estritos).
2. Criar uma nova sessão isolada e repetir o smoke editorial para avançar por `diagnose -> plan -> assemble -> review`.
3. Acompanhar a telemetria em `llm_usage.jsonl`, `agent_run.json` e os artefatos versionados em `.revisions/`.
4. Se a EDL editorial for aprovada, executar o fluxo determinístico completo (refiner, quatro QCs, readiness e publicação atômica de `timeline.xml`).
5. Validar a qualidade dos cortes manualmente no Premiere Pro.

Limitações conhecidas: ainda não há resume automático após crash; lock abandonado bloqueia a sessão de forma fail-closed; mídia totalmente silenciosa/sem words precisa de política explícita futura.

