### Task 2: CLI Integration

**Files:**
- Modify: `src/fireclaw_core/rag/rag_cli.py`
- Modify: `tests/test_rag_dense_cli.py`

**Interfaces:**
- Consumes:
  - `load_dense_eval_cases(path: Path) -> list[DenseEvalCase]`
  - `evaluate_dense_retriever(retriever: Any, cases: list[DenseEvalCase], *, top_k: int = 10) -> DenseEvalReport`
  - existing `_create_embedding_provider(provider_name: str, model_path: str | None = None)`
  - existing `DenseRetriever.load(index_dir: Path, provider: EmbeddingProvider)`
- Produces:
  - CLI command `eval-dense-index`
  - Helper `_cmd_eval_dense_index(args: argparse.Namespace) -> int`

- [ ] **Step 1: Write failing CLI test**

Append to `tests/test_rag_dense_cli.py`:

```python
def test_cli_eval_dense_index_writes_report_with_fake_provider(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider, build_dense_index
    from fireclaw_core.rag.rag_cli import main

    records_path = tmp_path / "records.jsonl"
    records_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "chunk_id": "chunk_rescue",
                        "parent_id": "parent_rescue",
                        "doc_id": "doc_rescue",
                        "clean_text": "rescue victim search",
                        "indexable": True,
                    }
                ),
                json.dumps(
                    {
                        "chunk_id": "chunk_smoke",
                        "parent_id": "parent_smoke",
                        "doc_id": "doc_smoke",
                        "clean_text": "smoke visibility",
                        "indexable": True,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, index_dir, FakeEmbeddingProvider(), batch_size=2)

    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "dense_zh_001",
                "topic": "rescue",
                "query": "rescue victim",
                "gold_parent_ids": ["parent_rescue"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "report.json"

    exit_code = main(
        [
            "eval-dense-index",
            "--provider",
            "fake",
            "--index-dir",
            str(index_dir),
            "--cases",
            str(cases_path),
            "--top-k",
            "5",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["case_count"] == 1
    assert report["hit_at_1"] == 1.0
    printed = json.loads(capsys.readouterr().out)
    assert printed["hit_at_1"] == 1.0
```

- [ ] **Step 2: Run CLI test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_cli_red tests/test_rag_dense_cli.py::test_cli_eval_dense_index_writes_report_with_fake_provider -q
```

Expected:

```text
argparse.ArgumentError or SystemExit because eval-dense-index is not a known command
```

- [ ] **Step 3: Add CLI parser and handler**

Modify `src/fireclaw_core/rag/rag_cli.py`.

Add imports:

```python
from fireclaw_core.rag.dense_eval import evaluate_dense_retriever
from fireclaw_core.rag.dense_eval import load_dense_eval_cases
```

Add parser after `dense_query` parser:

```python
    dense_eval = subparsers.add_parser("eval-dense-index", help="Evaluate dense retrieval against gold cases.")
    dense_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
    dense_eval.add_argument("--index-dir", default=None)
    dense_eval.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
    dense_eval.add_argument("--model-path", default=None)
    dense_eval.add_argument("--cases", default=None)
    dense_eval.add_argument("--top-k", type=int, default=10)
    dense_eval.add_argument("--output", default=None)
```

Add command dispatch:

```python
    if args.command == "eval-dense-index":
        return _cmd_eval_dense_index(args)
```

Add handler near `_cmd_query_dense_index`:

```python
def _cmd_eval_dense_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    index_dir = Path(args.index_dir) if args.index_dir else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
    output_path = Path(args.output) if args.output else None
    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    retriever = DenseRetriever.load(index_dir, provider)
    cases = load_dense_eval_cases(cases_path)
    report = evaluate_dense_retriever(retriever, cases, top_k=args.top_k)
    payload = report.to_dict()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_json_output(payload)
    return 0
```

- [ ] **Step 4: Run CLI test to verify it passes**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_cli_green tests/test_rag_dense_cli.py::test_cli_eval_dense_index_writes_report_with_fake_provider -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Run dense eval and dense CLI tests together**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_cli_final tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py -q
```

Expected:

```text
All tests pass
```

- [ ] **Step 6: Checkpoint without commit**

Run:

```powershell
git status --short --branch
```

Expected:

```text
M src/fireclaw_core/rag/rag_cli.py
M tests/test_rag_dense_cli.py
?? src/fireclaw_core/rag/dense_eval.py
?? tests/test_rag_dense_eval.py
```

The existing untracked spec and memory files may also appear.

---
