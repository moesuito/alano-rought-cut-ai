# Agentic Editorial Loop v0.6.0

## Objetivo

Substituir a dependência de uma única chamada extremamente competente por um processo editorial decomponível, verificável e compatível com modelos menores, inclusive locais.

A LLM recebe a transcrição pronta. O trabalho acústico e temporal continua determinístico e fora do agente.

## Implementação atual

`helpers/agentic_editor.py` mantém uma única conversa e executa três fases:

1. **Strategy:** lê brief e material completo, diagnostica o tipo de gravação, resolve retakes e propõe os blocos narrativos.
2. **Assembly:** transforma a estratégia em ranges concretos do EDL.
3. **Reflection:** critica a EDL e pode devolvê-la refinada; o ciclo repete até aprovação ou limite configurado.

Os artefatos esperados incluem:

- `editorial_strategy.json`;
- `edl.json`;
- `editorial_audit.txt`;
- histórico técnico em `session.log`.

## Separação dos prompts

- `helpers/prompts/agentic_editor_system_prompt.md`: identidade, conhecimento e princípios duráveis do editor.
- `helpers/prompts/agentic_prompts.py`: mensagens das fases, schemas JSON e regras dinâmicas de formato.

Essa fronteira deve permanecer: conhecimento editorial precisa poder evoluir sem ficar enterrado no motor Python.

O antigo `helpers/prompts/editor_system_prompt.md` pertence ao modo one-shot e será removido junto com esse modo quando a migração estiver validada.

## Próxima evolução

O loop atual ainda é uma primeira versão. A arquitetura desejada é:

```text
entender objetivo e restrições
  -> construir plano editorial
  -> decompor plano em tarefas/beats
  -> consultar evidências necessárias
  -> executar uma tarefa por vez
  -> montar EDL
  -> criticar contra plano, brief e transcrição
  -> corrigir lacunas
  -> validar estrutura e evidência
  -> entregar ao pipeline determinístico
```

O agente futuro deve conseguir pedir a si próprio evidências compactas, rastrear o que já resolveu e revisar localmente uma parte da timeline sem reconstruir todo o trabalho.

## Contratos fail-closed

Antes de considerar o agente aprovado:

- todas as respostas obrigatórias devem ser JSON válido e compatível com schema;
- todo range deve apontar para fonte e timestamps existentes;
- estratégia e EDL devem compartilhar beats identificáveis;
- limite de loops sem aprovação é falha/revisão, não sucesso;
- falha de parse nunca pode assumir `APPROVED`;
- a crítica precisa avaliar brief, estratégia, transcrição e EDL, não somente o JSON dos ranges;
- o orquestrador deve bloquear o XML até todos os gates determinísticos passarem.

## Estado conhecido

A branch v0.6 implementa a conversa multi-turno e o ciclo básico de reflexão. Ela ainda possui fallbacks fail-open e integração incompleta com os gates de readiness. O code review seguinte deve tratar isso antes de expandir a quantidade de agentes ou ferramentas.

## Modelos locais

O cliente usa `/chat/completions` OpenAI-compatible. Isso permite testar APIs atuais e, sem trocar o contrato do agente, apontar para Ollama, vLLM ou outro servidor local.

O projeto deve otimizar para:

- contexto compacto;
- tarefas pequenas;
- respostas estruturadas;
- validação determinística;
- retries limitados e observáveis;
- nenhuma exigência de reasoning oculto do provedor.
