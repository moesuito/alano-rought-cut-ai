# Contrato de ferramentas

O agente editorial não recebe shell nem acesso geral ao sistema. O host expõe três operações mínimas, todas bloqueadas por padrão fora das raízes autorizadas.

## `read_file`

```json
{"path": "takes_packed.md"}
```

Lê UTF-8 somente de:

- módulos explicitamente autorizados pelo `manifest.json` para a fase;
- entradas da sessão: `brief.md`, `takes_packed.md`, `transcripts/*.json` e `edl_template.json`.

`read_file` nunca lê artefatos. `diagnosis.json`, `cut_plan.json`, `edl.draft.json` e revisões anteriores são acessados exclusivamente por `read_artifact`.

O host resolve o path contra uma raiz conhecida, rejeita absoluto, `..`, escape por symlink, extensão não permitida, arquivo acima do limite e qualquer segredo. O retorno inclui path canônico relativo, conteúdo e hash quando disponível.

## `read_artifact`

```json
{"name": "cut_plan.json", "revision": 2}
```

Lê uma revisão validada do diretório de artefatos da sessão. A revisão omitida significa a última revisão válida, nunca um arquivo parcial.

## `write_artifact`

```json
{
  "name": "edl.draft.json",
  "expected_revision": 0,
  "content": {"version": 1}
}
```

Grava somente um nome permitido em `contracts/artifacts.md`. O host deve:

1. validar o JSON contra o schema da fase;
2. aplicar limite de tamanho e profundidade;
3. rejeitar overwrite se `expected_revision` não corresponder;
4. gravar em arquivo temporário na mesma raiz;
5. fazer replace atômico;
6. registrar revisão, hash e relação com os inputs no ledger da execução.

O agente nunca escolhe um path de saída arbitrário. Não pode alterar `brief.md`, takes, transcrições, conhecimento, logs, `.env`, código ou artefato de outra sessão.

## Falhas

Erro de acesso, leitura truncada, conflito de revisão, schema inválido ou gravação incompleta bloqueia a fase. O agente pode corrigir o payload e tentar novamente dentro do limite configurado; não pode declarar aprovação com base em uma ferramenta que falhou.
