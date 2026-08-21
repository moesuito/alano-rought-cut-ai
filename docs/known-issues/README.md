# Known Issues

Status: active.
Updated: 2026-08-20

Registre aqui problemas encontrados em testes manuais ou revisões. Cada problema ocupa um arquivo e entra no índice, preservando evidências e decisões entre sessões de desenvolvimento.

Esta pasta contém problemas **transversais** do produto. Notas locais permanecem na task ou pull request que as originou; qualquer problema não resolvido que afete o produto pertence aqui.

## Index

| ID | Area | Title | Priority | Status |
| --- | --- | --- | --- | --- |
| ISSUE-001 | Audio model | Vendored `cb.rnnn` diverges from documented hash | P1 | reported |

## Conventions

- One issue per file: `ISSUE-###-short-slug.md`.
- Preserve both the failing behavior and the expected behavior, with evidence.
- Status values: `reported`, `planned`, `in-progress`, `reopened`, `resolved`, `archived`.
- Keep evidence separate from a proposed fix and identify the commit or test that resolves the issue.
