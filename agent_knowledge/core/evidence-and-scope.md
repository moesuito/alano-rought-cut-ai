# Evidência e limite de atuação

## Entrada presumida

O pipeline já entregou transcrições temporizadas. O agente começa no conteúdo editorial: não inventa mídia, não inicia transcrição e não decide configuração de ASR.

As transcrições canônicas em `transcripts/<source>.json` são a fonte de verdade direta para palavras individuais, pausas e timestamps. O agente opera diretamente sobre as evidências temporizadas recebidas.

## Limite de percepção

Transcrição não prova enquadramento, expressão facial, foco, iluminação, gesto, ação em tela, energia vocal ou ruído não transcrito. Não descreva nem use esses sinais sem uma evidência explícita de outra entrada futura.

A transcrição pode conter erros. Trate grafia inesperada, nomes próprios, números, termos técnicos e mudanças abruptas como possíveis incertezas de ASR. Não “corrija” um timestamp nem uma citação por intuição. Compare contexto e retakes; se a exatidão mudar o sentido e não houver evidência suficiente, marque revisão humana.

## Autoridades

- O brief tem prioridade para objetivo, público, restrições e itens obrigatórios.
- O conteúdo das transcrições canônicas é a autoridade sobre o que foi dito e sobre os limites temporais das palavras.
- O plano aprovado é a autoridade sobre beats e intenção durante a montagem.
- A EDL é a autoridade editorial entregue ao pipeline técnico.
- Helpers determinísticos são a autoridade sobre snapping acústico, frames, QC e XML.

## Limites de saída

- Selecione somente IDs de fonte presentes na entrada.
- Use apenas ranges positivos com `end > start` e limites sustentados pela evidência temporal.
- Não prometa sample accuracy, limpeza de áudio ou perfeição visual.
- Não escreva paths de mídia, segredos, tokens ou conteúdo fora do diretório de artefatos.
- Nunca trate uma inferência como fato. Registre-a como hipótese e a confiança correspondente.
