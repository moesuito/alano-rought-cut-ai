# ISSUE-001 — `cb.rnnn` diverge da proveniência registrada

Status: `resolved`

Priority: `P1`

Found: 2026-08-20

Resolved: 2026-08-21

## Comportamento observado

O arquivo rastreado `helpers/models/cb.rnnn` possui SHA-256:

```text
51BB92B9450988F5DE3DAC32422C3551CA61760C1EF042751037178F4DFAB3F8
```

`helpers/models/NOTICE` e `helpers/audio_analysis.py` registram como esperado:

```text
F1357C4E5BE9DEE8467BEAD486DFCED2D75B640C26AD0B594FA7F102322371D9
```

A suíte `py -3.12 -m pytest -q` termina com `301 passed, 1 skipped, 1 failed`; a única falha é `tests/test_f1_1.py::test_model_hash_preflight`.

## Impacto

O preflight rejeita corretamente o modelo vendorizado. Alterar a constante para aceitar o arquivo atual apagaria a garantia de proveniência e poderia permitir um modelo incorreto no refinamento de áudio.

## Resolução exigida

Recuperar `conjoined-burgers-2018-08-28/cb.rnnn` da origem e commit declarados no `NOTICE`, confirmar o SHA-256 esperado antes de substituir o binário e então repetir a suíte completa. Não baixar nem promover um arquivo alternativo sem atualizar proveniência, licença e evidência de equivalência.

## Diagnóstico

O objeto Git rastreado nunca foi um modelo alternativo. O blob
`bcbf6b56746dc6547751a960f1fec74735f33e02` é idêntico ao arquivo publicado
pelo upstream no commit documentado. Seu conteúdo bruto tem 299.741 bytes,
22 terminadores LF e o SHA-256 esperado.

No checkout Windows, `core.autocrlf=true` tratava o modelo ASCII como texto e
substituía os 22 terminadores LF por CRLF. O arquivo de trabalho passava a ter
299.763 bytes e o SHA-256 divergente. O preflight detectava corretamente essa
alteração de bytes.

## Resolução aplicada

- `.gitattributes` marca `helpers/models/*.rnnn` como `binary`, desativando
  conversão de texto, diff textual e merge textual.
- `helpers/models/cb.rnnn` foi restaurado byte a byte a partir do blob upstream
  verificado.
- o teste de regressão exige o artefato vendorizado, o hash upstream conhecido
  e a ausência de CRLF.

## Evidência de proveniência

- repositório: `https://github.com/GregorR/rnnoise-models`;
- commit fixado: `3eee541a283fd3b8f81b85b1748e3b9ccbefa04d`;
- arquivo: `conjoined-burgers-2018-08-28/cb.rnnn`;
- URL imutável: `https://raw.githubusercontent.com/GregorR/rnnoise-models/3eee541a283fd3b8f81b85b1748e3b9ccbefa04d/conjoined-burgers-2018-08-28/cb.rnnn`;
- blob Git SHA-1: `bcbf6b56746dc6547751a960f1fec74735f33e02`;
- SHA-256 do objeto upstream e do arquivo corrigido:
  `F1357C4E5BE9DEE8467BEAD486DFCED2D75B640C26AD0B594FA7F102322371D9`.

O README do upstream declara que os modelos, fora das ferramentas e do próprio
README, não são trabalho criativo sujeito a copyright. A atribuição e a origem
continuam preservadas em `helpers/models/NOTICE`.
