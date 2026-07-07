# FireClaw Dense Evaluation Final Review Package

Generated: 2026-07-07 12:19 +08:00

## Review Scope

- Dense evaluation core and tests.
- `eval-dense-index` CLI integration.
- 10-case evidence-first Chinese gold set.
- Real BGE-M3 evaluation report and memory/progress records.

## Current Git Status
```text
warning: could not open directory '.pytest_tmp/': Permission denied
## rag-dev...origin/rag-dev
 M src/fireclaw_core/rag/rag_cli.py
 M tests/test_rag_dense_cli.py
?? .superpowers/
?? data/rag/fire_rescue/eval/
?? docs/superpowers/plans/2026-07-07-fireclaw-dense-retrieval-evaluation.md
?? docs/superpowers/specs/2026-07-07-fireclaw-dense-retrieval-evaluation-design.md
?? memory/2026-07-07/
?? src/fireclaw_core/rag/dense_eval.py
?? tests/test_rag_dense_eval.py
```

## Tracked Diff Stat
```text
warning: in the working copy of 'src/fireclaw_core/rag/rag_cli.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'tests/test_rag_dense_cli.py', LF will be replaced by CRLF the next time Git touches it
 src/fireclaw_core/rag/rag_cli.py | 30 +++++++++++++++
 tests/test_rag_dense_cli.py      | 79 ++++++++++++++++++++++++++++++++++++++++
 2 files changed, 109 insertions(+)
```

## Tracked Diff
```diff
warning: in the working copy of 'src/fireclaw_core/rag/rag_cli.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'tests/test_rag_dense_cli.py', LF will be replaced by CRLF the next time Git touches it
diff --git a/src/fireclaw_core/rag/rag_cli.py b/src/fireclaw_core/rag/rag_cli.py
index dba6290..e08e0bf 100644
--- a/src/fireclaw_core/rag/rag_cli.py
+++ b/src/fireclaw_core/rag/rag_cli.py
@@ -11,6 +11,8 @@ from fireclaw_core.rag.chunking import ChunkingConfig
 from fireclaw_core.rag.corpus_chunking import chunk_corpus_pages
 from fireclaw_core.rag.corpus_extraction import extract_corpus_pages
 from fireclaw_core.rag.extraction import PdfTextExtractionError
+from fireclaw_core.rag.dense_eval import evaluate_dense_retriever
+from fireclaw_core.rag.dense_eval import load_dense_eval_cases
 from fireclaw_core.rag.dense_retrieval import DenseRetriever
 from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider
 from fireclaw_core.rag.dense_retrieval import build_dense_index
@@ -75,6 +77,15 @@ def main(argv: list[str] | None = None) -> int:
     dense_query.add_argument("--parents", action="store_true")
     dense_query.add_argument("--parent-chunks", default=None)

+    dense_eval = subparsers.add_parser("eval-dense-index", help="Evaluate dense retrieval against gold cases.")
+    dense_eval.add_argument("--corpus-root", default="data/rag/fire_rescue")
+    dense_eval.add_argument("--index-dir", default=None)
+    dense_eval.add_argument("--provider", choices=["fake", "bge-m3"], default="fake")
+    dense_eval.add_argument("--model-path", default=None)
+    dense_eval.add_argument("--cases", default=None)
+    dense_eval.add_argument("--top-k", type=int, default=10)
+    dense_eval.add_argument("--output", default=None)
+
     args = parser.parse_args(argv)
     if args.command == "extract-pages":
         return _cmd_extract_pages(args)
@@ -86,6 +97,8 @@ def main(argv: list[str] | None = None) -> int:
         return _cmd_build_dense_index(args)
     if args.command == "query-dense-index":
         return _cmd_query_dense_index(args)
+    if args.command == "eval-dense-index":
+        return _cmd_eval_dense_index(args)
     return 1


@@ -193,6 +206,23 @@ def _cmd_query_dense_index(args: argparse.Namespace) -> int:
     return 0


+def _cmd_eval_dense_index(args: argparse.Namespace) -> int:
+    corpus_root = Path(args.corpus_root)
+    index_dir = Path(args.index_dir) if args.index_dir else corpus_root / "indexes" / "dense" / _provider_index_name(args.provider)
+    cases_path = Path(args.cases) if args.cases else corpus_root / "eval" / "dense_gold_cases_zh_v1.jsonl"
+    output_path = Path(args.output) if args.output else None
+    provider = _create_embedding_provider(args.provider, model_path=args.model_path)
+    retriever = DenseRetriever.load(index_dir, provider)
+    cases = load_dense_eval_cases(cases_path)
+    report = evaluate_dense_retriever(retriever, cases, top_k=args.top_k)
+    payload = report.to_dict()
+    if output_path is not None:
+        output_path.parent.mkdir(parents=True, exist_ok=True)
+        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
+    _write_json_output(payload)
+    return 0
+
+
 def _create_embedding_provider(provider_name: str, *, model_path: str | None = None):
     if provider_name == "fake":
         return FakeEmbeddingProvider()
diff --git a/tests/test_rag_dense_cli.py b/tests/test_rag_dense_cli.py
index 23da9ca..b4ff0c5 100644
--- a/tests/test_rag_dense_cli.py
+++ b/tests/test_rag_dense_cli.py
@@ -3,6 +3,9 @@ from __future__ import annotations
 from io import BytesIO
 from io import TextIOWrapper
 import json
+from pathlib import Path
+
+import pytest

 from fireclaw_core.rag.rag_cli import _write_json_output
 from fireclaw_core.rag.rag_cli import main
@@ -86,6 +89,82 @@ def test_cli_build_and_query_dense_index_with_fake_provider(tmp_path, capsys):
     assert query_output["hits"][0]["record"]["chunk_id"] == "chunk-rescue"


+def test_cli_eval_dense_index_writes_report_with_fake_provider(
+    tmp_path: Path,
+    capsys: pytest.CaptureFixture[str],
+) -> None:
+    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider, build_dense_index
+
+    records_path = tmp_path / "records.jsonl"
+    records_path.write_text(
+        "\n".join(
+            [
+                json.dumps(
+                    {
+                        "chunk_id": "chunk_rescue",
+                        "parent_id": "parent_rescue",
+                        "doc_id": "doc_rescue",
+                        "clean_text": "rescue victim search",
+                        "indexable": True,
+                    }
+                ),
+                json.dumps(
+                    {
+                        "chunk_id": "chunk_smoke",
+                        "parent_id": "parent_smoke",
+                        "doc_id": "doc_smoke",
+                        "clean_text": "smoke visibility",
+                        "indexable": True,
+                    }
+                ),
+            ]
+        )
+        + "\n",
+        encoding="utf-8",
+    )
+    index_dir = tmp_path / "dense_index"
+    build_dense_index(records_path, index_dir, FakeEmbeddingProvider(), batch_size=2)
+
+    cases_path = tmp_path / "cases.jsonl"
+    cases_path.write_text(
+        json.dumps(
+            {
+                "case_id": "dense_zh_001",
+                "topic": "rescue",
+                "query": "rescue victim",
+                "gold_parent_ids": ["parent_rescue"],
+            },
+            ensure_ascii=False,
+        )
+        + "\n",
+        encoding="utf-8",
+    )
+    output_path = tmp_path / "report.json"
+
+    exit_code = main(
+        [
+            "eval-dense-index",
+            "--provider",
+            "fake",
+            "--index-dir",
+            str(index_dir),
+            "--cases",
+            str(cases_path),
+            "--top-k",
+            "10",
+            "--output",
+            str(output_path),
+        ]
+    )
+
+    assert exit_code == 0
+    report = json.loads(output_path.read_text(encoding="utf-8"))
+    assert report["case_count"] == 1
+    assert report["hit_at_1"] == 1.0
+    printed = json.loads(capsys.readouterr().out)
+    assert printed["hit_at_1"] == 1.0
+
+
 def test_write_json_output_replaces_unencodable_characters_for_gbk_stream():
     raw = BytesIO()
     stream = TextIOWrapper(raw, encoding="gbk", errors="strict", newline="")
```

## New/Untracked Files to Inspect
```text
src/fireclaw_core/rag/dense_eval.py
tests/test_rag_dense_eval.py
data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl
data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
docs/superpowers/specs/2026-07-07-fireclaw-dense-retrieval-evaluation-design.md
docs/superpowers/plans/2026-07-07-fireclaw-dense-retrieval-evaluation.md
.superpowers/sdd/progress.md
.superpowers/sdd/task-4-report.md
memory/2026-07-07/fireclaw-dense-index-status-check.md
```

## Verification Evidence

- Unit command: `python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_eval_review_fix_full_20260707 tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q`
- Unit result: `20 passed in 0.50s`
- Real eval command: `.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`
- Real eval result: exit code `0`, report written.
- Metrics: `case_count=10`, `hit_at_1=0.2`, `hit_at_5=0.4`, `hit_at_10=0.6`, `mrr_at_10=0.323611`, `gold_recall_at_10=0.6`.
