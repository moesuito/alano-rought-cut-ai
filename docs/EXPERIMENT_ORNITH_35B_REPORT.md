# Relatório de Experimento: Ornith 1.0 35B A3B (Local Vulkan / llama.cpp)

**Data:** 21 de Agosto de 2026  
**Ambiente:** Windows 11, GPU AMD/Vulkan, llama-server (b8089)  
**Modelo:** Ornith-1.0-35B-A3B-Q4_K_M.gguf  
**Modo de Raciocínio:** Desativado (sem tokens de thinking)  
**Taxa Média de Geração:** ~48 a 54 tokens/segundo  
**Branch:** codex/feature-v0.6.0-agentic-editor

---

## 1. Sumário Executivo

O experimento avaliou a viabilidade do modelo local **Ornith 1.0 35B** operando diretamente sobre a **transcrição palavra a palavra** (eliminando o estágio intermediário algorítmico takes_packed.md).

### Principais Conclusões:
1. **Compreensão Editorial Superior (Fases 1 e 2):**
   - O modelo demonstrou alta capacidade narrativa no diagnóstico (`diagnosis.json`) e planejamento (`cut_plan.json`).
   - Identificou com precisão cirúrgica os 5 beats narrativos, selecionou os takes vencedores e filtrou cacos de produção ("Corta", "Take 6, gravando", "Pessoal, só para o editor").
2. **Desempenho de Inferência:**
   - Com raciocínio desativado, o modelo atingiu ~50 tokens/s constantes na GPU local via Vulkan, sem travamento de contexto nem estouro de VRAM.
3. **Ponto Crítico / Gargalo Observado (Fase 3 - Montagem da EDL):**
   - Ao operar sem teto de tokens (`max_tokens = None`) e sem `repeat_penalty` calibrada, o modelo entrou em loop de repetição na fase de montagem da EDL ao fechar listas JSON extensas, gerando dezenas de milhares de tokens repetitivos.
   - Chamadas com restrições rígidas de schema exigem feedback semântico preciso e penalidade de repetição para evitar alucinações de loop em chamadas de escrita estruturada.

---

## 2. Resultados Detalhados por Fase

### Fase 1: Diagnóstico (diagnosis.json)
* **Status:** Aprovado e Validado com Sucesso
* **Tokens Decodificados:** 5.796 tokens (~112 segundos)
* **Qualidade Editorial:**
  * Classificou corretamente o vídeo como educational_explainer.
  * Detectou 5 famílias de retake e escolheu os vencedores corretos.
  * Mapeou 16 falas de bastidor como must_avoid e production_speech.
  * Validado 100% no validador semântico de integridade e evidência.

### Fase 2: Plano de Corte (cut_plan.json)
* **Status:** Aprovado e Validado com Sucesso
* **Tokens Decodificados:** 13.432 tokens (~275 segundos)
* **Qualidade Editorial:**
  * Definiu requisitos de seleção e intenção de transição para cada beat.
  * Mapeou 10 exclusões globais cobrindo todos os intervalos descartados.
  * Aprovado sem nenhuma ressalva de schema ou contrato.

### Fase 3: Montagem da EDL (edl.draft.json)
* **Status:** Instabilidade / Loop de Repetição
* **Comportamento Observado:**
  * Tentativa 1 gerou 13.619 tokens com ranges bem construídos, falhando apenas por uma discrepância semântica de pontuação/tempo.
  * Na tentativa de correção com remoção total de limites de tokens, o modelo entrou em repetição infinita de blocos JSON (>45.000 tokens gerados), exigindo cancelamento.

---

## 3. Alterações de Infraestrutura Implementadas na Sessão

1. **Abstração Direta de Transcrições Brutas:**
   - Remoção do acoplamento obrigatório com takes_packed.md.
   - As tools e instruções foram atualizadas para que o agente leia diretamente os JSONs de transcrição (`inputs/transcripts/SRC_*.json`).
2. **Ajustes de Timeout e Tokens:**
   - Suporte a chamadas longas com timeout configurável até 1200s (20 minutos) no `LLMClient` e `ArtifactAgent`.
3. **Feedback Semântico Enriquecido:**
   - O validador de artefatos agora repassa `exc.message` exata nas rejeições de schema e semântica, permitindo auto-correção precisa.
4. **Isolamento de Processos:**
   - Rotina estrita de gerenciamento e término seguro do `llama-server.exe` e runners concorrentes.

---

## 4. Recomendações para Próximos Testes / Abordagens

1. **Ajuste de Hiperparâmetros no llama-server:**
   - Definir `repeat_penalty: 1.1` a `1.15` para geração estruturada.
   - Definir teto de segurança (`max_tokens`: 12.000 a 16.000), suficiente para vídeos longos sem permitir loops infinitos.
2. **Grammars BNF / JSON Schema Restrito no Backend:**
   - Utilizar `--grammar` ou `response_format` nativo do llama.cpp para forçar fechamento sintático de objetos EDL.
3. **Modelos Alternativos:**
   - Avaliar modelos instrucionais adicionais (Qwen 2.5 32B Coder/Instruct, Llama 3.3 70B quantizado ou modelos editoriais dedicados).
