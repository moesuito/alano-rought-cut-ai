# Completed Tasks

| Task | Commit | Validation | Notes |
| --- | --- | --- | --- |
| F1.1 | `0ef7847`, `86bfde5` | `pytest tests/test_f1_1.py -q` (11 passed); `compileall`; helper CLI checks; `git diff --check` | Accepted by ORCHESTRATOR. |
| F1.2 | `a23b288`, `25f41c3` | `pytest -q` (16 passed); `compileall`; renderer/QC CLI checks; `git diff --check` | Accepted by ORCHESTRATOR. |
| F1.3 | `acaf01a` | `pytest -q` (25 passed); `compileall`; semantic/readiness CLI checks | Accepted by ORCHESTRATOR. |
| F1.4 | `060e08e`, `027d5ca` | `pytest -q` (30 passed, 1 skipped); `compileall`; XML/readiness CLI checks; update/init smoke | Reopened by manual Premiere regression; implementation commits preserved as baseline. |
| Baseline checkpoint | `3b9b904` | Clean worktree | State before join-regression corrections. |
| F1.5 | `cabe8d6` | `pytest tests/test_f1_5.py -q` (17 passed); `pytest -q` (47 passed, 1 skipped); compileall; helper CLI checks; commit diff check | Accepted after two review cycles. Broker timed out after the final amend, but report, commit, diff, and independent validation were complete. |
