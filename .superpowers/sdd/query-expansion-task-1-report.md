# FireClaw Dense Query Expansion Task 1 Report

## What I Implemented

- Added `src/fireclaw_core/rag/query_expansion.py`.
- Added `QueryExpansion` and `QueryVariant` dataclasses.
- Added `load_query_expansions(path: Path) -> dict[str, QueryExpansion]` for UTF-8/UTF-8-SIG JSONL loading.
- Added duplicate `case_id`, missing `case_id`, missing file, malformed JSONL, and invalid `terms` list validation.
- Added `build_query_variants(...)` for ordered `zh`, `en`, and `terms` query variant construction.
- Added `effective_query_en` behavior that prefers `reviewed_query_en` over `llm_query_en`.
- Added `require_reviewed=True` guard that rejects non-`reviewed` expansion rows before using expanded query variants.
- Added `tests/test_rag_query_expansion.py` exactly covering the loading and query selection behaviors from the task brief.

## TDD Evidence

### RED

Command:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_query_expansion_red tests/test_rag_query_expansion.py -q
```

Output:

```text
=================================== ERRORS ====================================
_____________ ERROR collecting tests/test_rag_query_expansion.py ______________
ImportError while importing test module 'C:\Users\L\Desktop\lpp\fireclaw-master\tests\test_rag_query_expansion.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
E:\anaconda3\Lib\importlib\__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests\test_rag_query_expansion.py:9: in <module>
    from fireclaw_core.rag.query_expansion import QueryExpansion
E   ModuleNotFoundError: No module named 'fireclaw_core.rag.query_expansion'
=========================== short test summary info ===========================
ERROR tests/test_rag_query_expansion.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.28s
```

Why expected:

- The tests were written before production code.
- `src/fireclaw_core/rag/query_expansion.py` did not exist yet.
- The failure is the expected missing-module failure from the task brief.

### GREEN

Initial sandboxed command hit a Windows temp-directory permission issue after test execution started:

```text
EE...                                                                    [100%]
PermissionError: [WinError 5] ... 'C:\\Users\\L\\AppData\\Local\\Temp\\pytest_tmp_query_expansion_green'
```

Root cause:

- The brief command places `--basetemp` under `$env:TEMP`.
- In the managed sandbox, pytest cleanup/iteration of that temp path was denied.
- This was environmental, not a production-code assertion failure.

Rerun with the same command outside the sandbox:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_query_expansion_green tests/test_rag_query_expansion.py -q
```

Output:

```text
.....                                                                    [100%]
5 passed in 0.29s
```

## Files Changed

- `src/fireclaw_core/rag/query_expansion.py`
- `tests/test_rag_query_expansion.py`
- `.superpowers/sdd/query-expansion-task-1-report.md`

## Self-Review Findings

- The implementation is scoped to the requested module and test file.
- No CLI, dense ranking, dense eval, data JSONL, docs, or memory files were modified.
- The test API matches the task brief.
- `build_query_variants` preserves requested variant order.
- `zh` queries do not require an expansion row; `en` and `terms` do require one.
- `require_reviewed=True` checks `status == "reviewed"` before allowing expanded variants.
- `git diff -- src/fireclaw_core/rag/query_expansion.py tests/test_rag_query_expansion.py` produced no output because both files are new and untracked. Additional scoped status confirmed:

```text
?? src/fireclaw_core/rag/query_expansion.py
?? tests/test_rag_query_expansion.py
```

## Any Concerns

- GREEN verification required non-sandbox execution because the brief's `$env:TEMP` basetemp path was not fully writable/iterable in the managed sandbox.
- No commits were created, as required.
