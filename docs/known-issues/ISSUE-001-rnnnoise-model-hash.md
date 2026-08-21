# ISSUE-001 — `cb.rnnn` diverge da proveniência registrada

Status: `reported`

Priority: `P1`

Found: 2026-08-20

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
