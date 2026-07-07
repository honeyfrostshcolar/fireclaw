# Task 2 Report - CLI Integration

## Summary

Implemented `eval-dense-index` in `src/fireclaw_core/rag/rag_cli.py` and added a CLI regression test in `tests/test_rag_dense_cli.py`.

## RED Evidence

### Command

```powershell
$env:PYTHONPATH = ".deps;src"; $bt = Join-Path $env:TEMP 'pytest_tmp_dense_eval_cli_red'; python -m pytest --basetemp=$bt tests/test_rag_dense_cli.py::test_cli_eval_dense_index_writes_report_with_fake_provider -q
```

### Result

Initial sandboxed run hit the known Windows pytest `basetemp` cleanup permission issue, so I reran with elevated permissions to capture the real failure:

```text
E           argparse.ArgumentError: argument command: invalid choice: 'eval-dense-index'
...
SystemExit: 2
```

This confirmed the test was failing for the expected reason: the CLI command did not exist yet.

## GREEN Evidence

### Command

```powershell
$env:PYTHONPATH = ".deps;src"; $bt = Join-Path $env:TEMP 'pytest_tmp_dense_eval_cli_green'; python -m pytest --basetemp=$bt tests/test_rag_dense_cli.py::test_cli_eval_dense_index_writes_report_with_fake_provider -q
```

### Result

```text
1 passed in 0.29s
```

## Full Verification

### Command

```powershell
$env:PYTHONPATH = ".deps;src"; $bt = Join-Path $env:TEMP 'pytest_tmp_dense_eval_cli_final'; python -m pytest --basetemp=$bt tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py -q
```

### Result

```text
13 passed in 0.29s
```

## File Changes

- `src/fireclaw_core/rag/rag_cli.py`
  - added `eval-dense-index` parser
  - added `_cmd_eval_dense_index`
  - wired dispatch and dense eval imports
- `tests/test_rag_dense_cli.py`
  - added CLI regression test for `eval-dense-index`
  - used `--top-k 10` to satisfy the dense eval contract
- `.superpowers/sdd/task-2-report.md`
  - this report

## Self-Check

- The new CLI test fails before the implementation and passes after it.
- The implementation is thin and delegates evaluation to `evaluate_dense_retriever` / `load_dense_eval_cases`.
- The test uses `--top-k 10`, matching the dense evaluation requirement.
- The Windows pytest `basetemp` permission issue was observed and worked around by rerunning with a temp-directory basetemp path and elevated permissions.
