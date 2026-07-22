### Task 3: CLI Integration for Hybrid Rerank Evaluation

**Files:**
- Modify: `src/fireclaw_core/rag/rag_cli.py`
- Modify: `tests/test_rag_bm25_cli.py`

**Interfaces:**
- Consumes:
  - `evaluate_hybrid_retrievers_with_rerank(...)`
  - `load_parent_texts(...)`
  - `FakeRerankerProvider`
  - `BGEFlagRerankerProvider`
- Produces:
  - CLI command `eval-hybrid-rerank-index`

- [ ] **Step 1: Write failing CLI test**

Append to `tests/test_rag_bm25_cli.py`:

```python
def test_cli_eval_hybrid_rerank_index_with_fake_reranker(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
    from fireclaw_core.rag.dense_retrieval import build_dense_index

    records_path = tmp_path / "records.jsonl"
    _write_jsonl(
        records_path,
        [
            _record("generic", "generic firefighter health rescue"),
            _record("gold", "SCBA rehabilitation medical evaluation NFPA 1584 rescue"),
        ],
    )
    dense_index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, dense_index_dir, FakeEmbeddingProvider(), batch_size=2)

    bm25_index_dir = tmp_path / "bm25_index"
    assert main(["build-bm25-index", "--records", str(records_path), "--index-dir", str(bm25_index_dir)]) == 0
    capsys.readouterr()

    parent_chunks_path = tmp_path / "parent_chunks.jsonl"
    _write_jsonl(
        parent_chunks_path,
        [
            {"parent_id": "generic__parent", "text": "generic firefighter health information"},
            {"parent_id": "gold__parent", "text": "SCBA rehabilitation medical evaluation NFPA 1584"},
        ],
    )

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "case",
                "topic": "rehab",
                "query": "中文问题",
                "gold_parent_ids": ["gold__parent"],
            }
        ],
    )
    expansions_path = tmp_path / "expansions.jsonl"
    _write_jsonl(
        expansions_path,
        [
            {
                "case_id": "case",
                "query_zh": "中文问题",
                "reviewed_query_en": "When should firefighters enter SCBA rehabilitation?",
                "term_query": "SCBA rehabilitation medical evaluation NFPA 1584",
                "terms": ["SCBA", "rehabilitation", "NFPA 1584"],
                "status": "reviewed",
            }
        ],
    )
    output_path = tmp_path / "hybrid_rerank_report.json"

    exit_code = main(
        [
            "eval-hybrid-rerank-index",
            "--provider",
            "fake",
            "--dense-index-dir",
            str(dense_index_dir),
            "--bm25-index-dir",
            str(bm25_index_dir),
            "--reranker-provider",
            "fake",
            "--parent-chunks",
            str(parent_chunks_path),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--dense-query-variants",
            "zh,en",
            "--bm25-query-variants",
            "en,terms",
            "--rerank-query-variant",
            "en",
            "--rerank-pool-size",
            "10",
            "--top-k",
            "10",
            "--require-reviewed-expansions",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["retrieval_method"] == "hybrid_rerank"
    assert report["retrieval_config"]["reranker"]["provider"] == "fake-reranker"
    assert report["retrieval_config"]["rerank_query_variant"] == "en"
    assert report["retrieval_config"]["rerank_pool_size"] == 10
    assert report["hit_at_1"] == 1.0
    assert report["results"][0]["top_hits"][0]["parent_id"] == "gold__parent"
    assert json.loads(capsys.readouterr().out)["retrieval_config"]["retrieval_method"] == "hybrid_rerank"
```

- [ ] **Step 2: Run CLI test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_cli_red tests/test_rag_bm25_cli.py::test_cli_eval_hybrid_rerank_index_with_fake_reranker -q
```

Expected:

```text
argparse error because command eval-hybrid-rerank-index does not exist
```

- [ ] **Step 3: Add CLI imports**

Modify `src/fireclaw_core/rag/rag_cli.py` imports:

```python
from fireclaw_core.rag.rerank_eval import evaluate_hybrid_retrievers_with_rerank
from fireclaw_core.rag.reranking import BGEFlagRerankerProvider
from fireclaw_core.rag.reranking import FakeRerankerProvider
from fireclaw_core.rag.reranking import load_parent_texts
```

- [ ] **Step 4: Add parser for `eval-hybrid-rerank-index`**

Inside `main(...)`, after the existing `hybrid_eval` parser block, add:

```python
hybrid_rerank_eval = subparsers.add_parser(
    "eval-hybrid-rerank-index",
    help="Evaluate hybrid dense+BM25 retrieval with parent-level reranking.",
)
hybrid_rerank_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
hybrid_rerank_eval.add_argument("--dense-index-dir", default=None)
hybrid_rerank_eval.add_argument("--bm25-index-dir", default=None)
hybrid_rerank_eval.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
hybrid_rerank_eval.add_argument("--model-path", default=None)
hybrid_rerank_eval.add_argument("--reranker-provider", choices=["fake", "bge-reranker"], default="fake")
hybrid_rerank_eval.add_argument("--reranker-model-path", default=None)
hybrid_rerank_eval.add_argument("--reranker-device", default=None)
hybrid_rerank_eval.add_argument("--reranker-batch-size", type=int, default=32)
hybrid_rerank_eval.add_argument("--reranker-max-length", type=int, default=512)
hybrid_rerank_eval.add_argument("--parent-chunks", default=None)
hybrid_rerank_eval.add_argument("--cases", default=None)
hybrid_rerank_eval.add_argument("--query-expansions", required=True)
hybrid_rerank_eval.add_argument("--dense-query-variants", default="zh,en,terms")
hybrid_rerank_eval.add_argument("--bm25-query-variants", default="en,terms")
hybrid_rerank_eval.add_argument("--rerank-query-variant", choices=["zh", "en", "terms"], default="en")
hybrid_rerank_eval.add_argument("--small-top-k", type=int, default=50)
hybrid_rerank_eval.add_argument("--rerank-pool-size", type=int, default=50)
hybrid_rerank_eval.add_argument("--top-k", type=int, default=10)
hybrid_rerank_eval.add_argument("--rrf-k", type=int, default=60)
hybrid_rerank_eval.add_argument("--max-passage-chars", type=int, default=6000)
hybrid_rerank_eval.add_argument("--output", default=None)
hybrid_rerank_eval.add_argument("--require-reviewed-expansions", action="store_true")
```

Add command dispatch:

```python
if args.command == "eval-hybrid-rerank-index":
    return _cmd_eval_hybrid_rerank_index(args)
```

- [ ] **Step 5: Add reranker provider and command helpers**

Add to `src/fireclaw_core/rag/rag_cli.py`:

```python
def _cmd_eval_hybrid_rerank_index(args: argparse.Namespace) -> int:
    corpus_root = Path(args.corpus_root)
    dense_index_dir = (
        Path(args.dense_index_dir)
        if args.dense_index_dir
        else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
    )
    bm25_index_dir = _bm25_index_dir(corpus_root, args.bm25_index_dir)
    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
    output_path = Path(args.output) if args.output else None
    expansions_path = Path(args.query_expansions)
    parent_chunks_path = Path(args.parent_chunks) if args.parent_chunks else corpus_root / "chunks" / "parent_chunks.jsonl"

    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
    dense_retriever = DenseRetriever.load(dense_index_dir, provider)
    bm25_retriever = BM25Retriever.load(bm25_index_dir)
    reranker = _create_reranker_provider(
        args.reranker_provider,
        model_path=args.reranker_model_path,
        device=args.reranker_device,
        batch_size=args.reranker_batch_size,
        max_length=args.reranker_max_length,
    )
    cases = load_dense_eval_cases(cases_path)
    expansions = load_query_expansions(expansions_path)
    parent_texts = load_parent_texts(parent_chunks_path)
    report = evaluate_hybrid_retrievers_with_rerank(
        dense_retriever,
        bm25_retriever,
        reranker,
        cases,
        query_expansions=expansions,
        parent_texts=parent_texts,
        dense_query_variants=_parse_csv_arg(args.dense_query_variants),
        bm25_query_variants=_parse_csv_arg(args.bm25_query_variants),
        rerank_query_variant=args.rerank_query_variant,
        rerank_pool_size=args.rerank_pool_size,
        top_k=args.top_k,
        small_top_k=args.small_top_k,
        rrf_k=args.rrf_k,
        max_passage_chars=args.max_passage_chars,
        require_reviewed_expansions=args.require_reviewed_expansions,
        query_expansions_path=str(expansions_path),
        parent_chunks_path=str(parent_chunks_path),
    )
    payload = report.to_dict()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_json_output(payload)
    return 0


def _create_reranker_provider(
    provider_name: str,
    *,
    model_path: str | None = None,
    device: str | None = None,
    batch_size: int = 32,
    max_length: int = 512,
):
    if provider_name == "fake":
        return FakeRerankerProvider()
    if provider_name == "bge-reranker":
        path = Path(model_path) if model_path else Path(".cache/models/bge-reranker-v2-m3")
        return BGEFlagRerankerProvider(
            path,
            device=device,
            batch_size=batch_size,
            max_length=max_length,
        )
    raise ValueError(f"Unsupported reranker provider: {provider_name}")
```

- [ ] **Step 6: Run CLI tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=.pytest_tmp_rerank_cli_green tests/test_rag_bm25_cli.py tests/test_rag_rerank_eval.py tests/test_rag_reranking.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 7: Checkpoint without committing**

Run:

```powershell
git diff -- src/fireclaw_core/rag/rag_cli.py tests/test_rag_bm25_cli.py
```

Expected:

```text
diff output shows only the new hybrid rerank CLI command, provider factory, and CLI test
```

---
