# Roadmap

Atualizado em 2026-08-20 para a linha v0.6.0.

## Agora: estabilizar a v0.6

1. Consolidar o repositório e remover duplicação documental.
2. Executar um code review completo do agente, orquestrador, transcrição, QC e instalação.
3. Tornar o pipeline autônomo fail-closed do parse da LLM até o XML.
4. Garantir que semantic QC, preview transcript QC e readiness sejam obrigatórios no caminho real.
5. Corrigir propagação de tipo de vídeo, pacing e identidade dos eventos acústicos.
6. Remover divergências de versão, paths e documentação.

## Próximo: inteligência editorial

1. Evoluir o agente de três chamadas/fases para um runtime com plano, tarefas, execução, crítica e validação explícitas.
2. Separar estado factual, memória de trabalho, plano editorial e histórico de decisões.
3. Criar schemas mais rigorosos para estratégia, beats, ranges e revisão.
4. Adicionar ferramentas internas para o agente consultar trechos, palavras, candidatos a retake e duração sem reenviar todo o contexto.
5. Projetar retry e recuperação sem transformar falha de parse em aprovação.
6. Melhorar o conhecimento do editor para Reels, YouTube, videoaulas, tutoriais e VSLs.
7. Medir qualidade e custo com modelos locais menores.

## Validação real

1. Construir um corpus privado representativo com permissões e resultados esperados.
2. Registrar erros editoriais, falhas acústicas, divergências de EDL e regressões.
3. Avaliar completude, coerência, seleção de retakes, pacing e segurança de joins.
4. Comparar modelos e configurações com o mesmo material e os mesmos gates.
5. Só promover uma release depois de validar o pipeline instalado, não apenas testes unitários.

## Limpeza controlada

1. Remover WhisperX e seus documentos antigos.
2. Remover ElevenLabs e AssemblyAI do produto depois de provar que nenhuma dependência ativa permanece.
3. Arquivar planos v0.4/v0.5 e manter somente documentação histórica necessária.
4. Remover o modo one-shot quando o loop agêntico tiver cobertura equivalente ou superior.
5. Revisar dependências, instalador e artefatos empacotados.

## Depois da estabilidade

- dispatcher para múltiplos vídeos e múltiplas entregas;
- integração mais direta com Premiere Pro;
- GUI desktop, com escolha futura entre Electron, Tauri ou alternativa equivalente;
- distribuição e atualização confiáveis para usuários finais.

Não priorizar acabamento da TUI atual: ela é um instrumento temporário de validação.
