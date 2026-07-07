# FireClaw Dense Retrieval Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first evidence-first evaluation harness for FireClaw's BGE-M3 dense retrieval index.

**Architecture:** Add a focused `fireclaw_core.rag.dense_eval` module that loads JSONL gold cases, evaluates ranked dense hits by `parent_id`, and serializes metrics reports. Extend `rag_cli.py` with `eval-dense-index` so the same dense retriever used by production smoke tests can run the 10-case Chinese gold set.

**Tech Stack:** Python 3.12, standard-library JSON/dataclasses/pathlib, existing `DenseRetriever`, existing `FakeEmbeddingProvider`, existing BGE-M3 provider for manual smoke only, pytest.

## Global Constraints

- Use evidence-first gold cases.
- First version has exactly 10 Chinese task-style queries.
- Dense retrieval only.
- Gold evidence is identified by `parent_id`.
- Required metrics are `Hit@1`, `Hit@5`, `Hit@10`, `MRR@10`, and `gold_recall@10`.
- Do not add BM25, hybrid fusion, reranking, answer generation, LLM judging, full manual annotation, `nDCG@K`, or graded `0/1/2/3` relevance in this plan.
- Dense scores are used only for ranking; do not introduce point-score thresholds.
- Unit tests must not require BGE-M3, GPU, network, or downloaded model files.
- Do not commit unless the user explicitly asks. Use `git status --short --branch` as the task checkpoint.

---

## File Structure

- Create `src/fireclaw_core/rag/dense_eval.py`
  - Owns evaluation dataclasses, JSONL case loading, metric computation, and retriever evaluation.
- Modify `src/fireclaw_core/rag/rag_cli.py`
  - Adds `eval-dense-index` command and JSON output/report writing.
- Create `tests/test_rag_dense_eval.py`
  - Unit tests for case loading, validation, metrics, and serialization.
- Modify `tests/test_rag_dense_cli.py`
  - CLI coverage for `eval-dense-index` using the fake provider and a tiny deterministic index.
- Create `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`
  - The first 10 evidence-first Chinese task-style cases.
- Update `memory/2026-07-07/fireclaw-dense-index-status-check.md`
  - Append commands, results, files modified, and next step after implementation.

---

### Task 1: Dense Evaluation Core

**Files:**
- Create: `src/fireclaw_core/rag/dense_eval.py`
- Create: `tests/test_rag_dense_eval.py`

**Interfaces:**
- Consumes:
  - `fireclaw_core.rag.dense_retrieval.DenseHit`
- Produces:
  - `DenseEvalCase`
  - `DenseEvalRetrievedHit`
  - `DenseEvalCaseResult`
  - `DenseEvalReport`
  - `load_dense_eval_cases(path: Path) -> list[DenseEvalCase]`
  - `evaluate_ranked_hits(cases: list[DenseEvalCase], hits_by_case_id: dict[str, list[DenseEvalRetrievedHit]], *, top_k: int = 10) -> DenseEvalReport`
  - `hits_from_dense_results(hits: list[DenseHit]) -> list[DenseEvalRetrievedHit]`
  - `evaluate_dense_retriever(retriever: Any, cases: list[DenseEvalCase], *, top_k: int = 10) -> DenseEvalReport`

- [ ] **Step 1: Write failing tests for case loading and validation**

Add this to `tests/test_rag_dense_eval.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.rag.dense_eval import (
    DenseEvalCase,
    DenseEvalRetrievedHit,
    evaluate_ranked_hits,
    load_dense_eval_cases,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_load_dense_eval_cases_accepts_valid_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    _write_jsonl(
        path,
        [
            {
                "case_id": "dense_zh_001",
                "topic": "smoke_victim_search",
                "query": "烟雾很大的房间里如何寻找被困人员？",
                "gold_parent_ids": ["parent_a"],
                "gold_chunk_ids": [],
                "expected_evidence_summary": "Victim search under smoke.",
                "source_doc_id": "doc_a",
                "notes": "Chinese task-style query.",
            }
        ],
    )

    cases = load_dense_eval_cases(path)

    assert cases == [
        DenseEvalCase(
            case_id="dense_zh_001",
            topic="smoke_victim_search",
            query="烟雾很大的房间里如何寻找被困人员？",
            gold_parent_ids=["parent_a"],
            gold_chunk_ids=[],
            expected_evidence_summary="Victim search under smoke.",
            source_doc_id="doc_a",
            notes="Chinese task-style query.",
        )
    ]


def test_load_dense_eval_cases_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    _write_jsonl(
        path,
        [
            {"case_id": "dup", "topic": "a", "query": "q1", "gold_parent_ids": ["p1"]},
            {"case_id": "dup", "topic": "b", "query": "q2", "gold_parent_ids": ["p2"]},
        ],
    )

    with pytest.raises(ValueError, match="duplicate case_id: dup"):
        load_dense_eval_cases(path)


def test_load_dense_eval_cases_rejects_empty_gold_parent_ids(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    _write_jsonl(path, [{"case_id": "bad", "topic": "a", "query": "q", "gold_parent_ids": []}])

    with pytest.raises(ValueError, match="gold_parent_ids"):
        load_dense_eval_cases(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_core_red tests/test_rag_dense_eval.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.dense_eval'
```

- [ ] **Step 3: Implement minimal case dataclass and loader**

Create `src/fireclaw_core/rag/dense_eval.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from fireclaw_core.rag.dense_retrieval import DenseHit


@dataclass(frozen=True)
class DenseEvalCase:
    case_id: str
    topic: str
    query: str
    gold_parent_ids: list[str]
    gold_chunk_ids: list[str] = field(default_factory=list)
    expected_evidence_summary: str = ""
    source_doc_id: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_dense_eval_cases(path: Path) -> list[DenseEvalCase]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dense eval case file not found: {path}")

    cases: list[DenseEvalCase] = []
    seen_case_ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc

            case_id = str(data.get("case_id") or "").strip()
            topic = str(data.get("topic") or "").strip()
            query = str(data.get("query") or "").strip()
            gold_parent_ids = [str(value).strip() for value in data.get("gold_parent_ids", []) if str(value).strip()]
            gold_chunk_ids = [str(value).strip() for value in data.get("gold_chunk_ids", []) if str(value).strip()]

            if not case_id:
                raise ValueError(f"Missing case_id in {path}:{line_no}")
            if case_id in seen_case_ids:
                raise ValueError(f"duplicate case_id: {case_id}")
            if not query:
                raise ValueError(f"Missing query for case_id: {case_id}")
            if not gold_parent_ids:
                raise ValueError(f"Missing gold_parent_ids for case_id: {case_id}")

            seen_case_ids.add(case_id)
            cases.append(
                DenseEvalCase(
                    case_id=case_id,
                    topic=topic,
                    query=query,
                    gold_parent_ids=gold_parent_ids,
                    gold_chunk_ids=gold_chunk_ids,
                    expected_evidence_summary=str(data.get("expected_evidence_summary") or ""),
                    source_doc_id=str(data.get("source_doc_id") or ""),
                    notes=str(data.get("notes") or ""),
                )
            )

    return cases
```

- [ ] **Step 4: Run tests to verify loader passes**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_core_green tests/test_rag_dense_eval.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Write failing tests for metrics**

Append to `tests/test_rag_dense_eval.py`:

```python
def test_evaluate_ranked_hits_computes_hit_mrr_and_gold_recall() -> None:
    cases = [
        DenseEvalCase(
            case_id="case_1",
            topic="thermal",
            query="烟雾中如何用热成像找人？",
            gold_parent_ids=["parent_gold"],
        ),
        DenseEvalCase(
            case_id="case_2",
            topic="mobility",
            query="机器人如何通过碎石地形？",
            gold_parent_ids=["parent_missing"],
        ),
    ]
    hits = {
        "case_1": [
            DenseEvalRetrievedHit(rank=1, score=0.9, chunk_id="c1", parent_id="parent_wrong", doc_id="d1"),
            DenseEvalRetrievedHit(rank=2, score=0.8, chunk_id="c2", parent_id="parent_gold", doc_id="d2"),
        ],
        "case_2": [
            DenseEvalRetrievedHit(rank=1, score=0.7, chunk_id="c3", parent_id="parent_other", doc_id="d3"),
        ],
    }

    report = evaluate_ranked_hits(cases, hits, top_k=10)

    assert report.case_count == 2
    assert report.hit_at_1 == 0.0
    assert report.hit_at_5 == 0.5
    assert report.hit_at_10 == 0.5
    assert report.mrr_at_10 == 0.25
    assert report.gold_recall_at_10 == 0.5
    assert report.results[0].first_gold_rank == 2
    assert report.results[1].first_gold_rank is None


def test_evaluate_ranked_hits_handles_multiple_gold_parents() -> None:
    cases = [
        DenseEvalCase(
            case_id="case_multi",
            topic="usar",
            query="废墟搜救机器人需要哪些能力？",
            gold_parent_ids=["parent_a", "parent_b"],
        )
    ]
    hits = {
        "case_multi": [
            DenseEvalRetrievedHit(rank=1, score=0.9, chunk_id="c1", parent_id="parent_a", doc_id="d1"),
            DenseEvalRetrievedHit(rank=2, score=0.8, chunk_id="c2", parent_id="parent_other", doc_id="d2"),
        ]
    }

    report = evaluate_ranked_hits(cases, hits, top_k=10)

    assert report.hit_at_1 == 1.0
    assert report.gold_recall_at_10 == 0.5
    assert report.results[0].retrieved_gold_parent_ids == ["parent_a"]


def test_dense_eval_report_serializes_to_dict() -> None:
    case = DenseEvalCase(
        case_id="case_1",
        topic="thermal",
        query="烟雾中如何用热成像找人？",
        gold_parent_ids=["parent_gold"],
    )
    hit = DenseEvalRetrievedHit(rank=1, score=0.95, chunk_id="chunk_gold", parent_id="parent_gold", doc_id="doc")

    report = evaluate_ranked_hits([case], {"case_1": [hit]}, top_k=10)
    data = report.to_dict()

    assert data["case_count"] == 1
    assert data["hit_at_1"] == 1.0
    assert data["results"][0]["top_hits"][0]["parent_id"] == "parent_gold"
```

- [ ] **Step 6: Run tests to verify metrics tests fail**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_metrics_red tests/test_rag_dense_eval.py -q
```

Expected:

```text
ImportError: cannot import name 'DenseEvalRetrievedHit'
```

- [ ] **Step 7: Implement metrics and serialization**

Extend `src/fireclaw_core/rag/dense_eval.py`:

```python
@dataclass(frozen=True)
class DenseEvalRetrievedHit:
    rank: int
    score: float
    chunk_id: str
    parent_id: str
    doc_id: str = ""
    source_file: str = ""
    page_start: int | None = None
    page_end: int | None = None
    heading: str = ""
    text_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DenseEvalCaseResult:
    case_id: str
    topic: str
    query: str
    gold_parent_ids: list[str]
    retrieved_parent_ids: list[str]
    retrieved_gold_parent_ids: list[str]
    first_gold_rank: int | None
    hit_at_1: bool
    hit_at_5: bool
    hit_at_10: bool
    mrr_at_10: float
    gold_recall_at_10: float
    top_hits: list[DenseEvalRetrievedHit]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["top_hits"] = [hit.to_dict() for hit in self.top_hits]
        return data


@dataclass(frozen=True)
class DenseEvalReport:
    case_count: int
    hit_at_1: float
    hit_at_5: float
    hit_at_10: float
    mrr_at_10: float
    gold_recall_at_10: float
    results: list[DenseEvalCaseResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "hit_at_1": round(self.hit_at_1, 6),
            "hit_at_5": round(self.hit_at_5, 6),
            "hit_at_10": round(self.hit_at_10, 6),
            "mrr_at_10": round(self.mrr_at_10, 6),
            "gold_recall_at_10": round(self.gold_recall_at_10, 6),
            "results": [result.to_dict() for result in self.results],
        }


def evaluate_ranked_hits(
    cases: list[DenseEvalCase],
    hits_by_case_id: dict[str, list[DenseEvalRetrievedHit]],
    *,
    top_k: int = 10,
) -> DenseEvalReport:
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    results: list[DenseEvalCaseResult] = []
    for case in cases:
        hits = sorted(hits_by_case_id.get(case.case_id, []), key=lambda hit: hit.rank)[:top_k]
        gold_ids = set(case.gold_parent_ids)
        retrieved_parent_ids = [hit.parent_id for hit in hits]
        retrieved_gold_parent_ids = sorted({parent_id for parent_id in retrieved_parent_ids if parent_id in gold_ids})
        first_gold_rank = next((hit.rank for hit in hits if hit.parent_id in gold_ids), None)
        hit_at_1 = any(hit.parent_id in gold_ids for hit in hits[:1])
        hit_at_5 = any(hit.parent_id in gold_ids for hit in hits[:5])
        hit_at_10 = any(hit.parent_id in gold_ids for hit in hits[:10])
        mrr_at_10 = (1.0 / first_gold_rank) if first_gold_rank is not None and first_gold_rank <= 10 else 0.0
        gold_recall_at_10 = len(retrieved_gold_parent_ids) / len(gold_ids)
        results.append(
            DenseEvalCaseResult(
                case_id=case.case_id,
                topic=case.topic,
                query=case.query,
                gold_parent_ids=case.gold_parent_ids,
                retrieved_parent_ids=retrieved_parent_ids,
                retrieved_gold_parent_ids=retrieved_gold_parent_ids,
                first_gold_rank=first_gold_rank,
                hit_at_1=hit_at_1,
                hit_at_5=hit_at_5,
                hit_at_10=hit_at_10,
                mrr_at_10=mrr_at_10,
                gold_recall_at_10=gold_recall_at_10,
                top_hits=hits,
            )
        )

    case_count = len(results)
    if case_count == 0:
        return DenseEvalReport(
            case_count=0,
            hit_at_1=0.0,
            hit_at_5=0.0,
            hit_at_10=0.0,
            mrr_at_10=0.0,
            gold_recall_at_10=0.0,
            results=[],
        )

    return DenseEvalReport(
        case_count=case_count,
        hit_at_1=sum(1 for result in results if result.hit_at_1) / case_count,
        hit_at_5=sum(1 for result in results if result.hit_at_5) / case_count,
        hit_at_10=sum(1 for result in results if result.hit_at_10) / case_count,
        mrr_at_10=sum(result.mrr_at_10 for result in results) / case_count,
        gold_recall_at_10=sum(result.gold_recall_at_10 for result in results) / case_count,
        results=results,
    )
```

- [ ] **Step 8: Run tests to verify metrics pass**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_metrics_green tests/test_rag_dense_eval.py -q
```

Expected:

```text
6 passed
```

- [ ] **Step 9: Write failing tests for conversion from dense hits and retriever runner**

Append to `tests/test_rag_dense_eval.py`:

```python
from fireclaw_core.rag.dense_eval import (
    evaluate_dense_retriever,
    hits_from_dense_results,
)
from fireclaw_core.rag.dense_retrieval import DenseHit


def test_hits_from_dense_results_preserves_parent_id_and_preview() -> None:
    dense_hit = DenseHit(
        rank=1,
        score=0.77,
        record={
            "chunk_id": "chunk_1",
            "parent_id": "parent_1",
            "doc_id": "doc_1",
            "source_file": "raw/source.pdf",
            "page_start": 3,
            "page_end": 4,
            "heading": "Victim Search",
            "clean_text": "A" * 300,
        },
    )

    converted = hits_from_dense_results([dense_hit])

    assert converted[0].rank == 1
    assert converted[0].score == 0.77
    assert converted[0].chunk_id == "chunk_1"
    assert converted[0].parent_id == "parent_1"
    assert converted[0].text_preview == "A" * 240


def test_evaluate_dense_retriever_runs_queries() -> None:
    class FakeRetriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            assert query == "机器人如何在烟雾中找人？"
            assert top_k == 10
            return [
                DenseHit(
                    rank=1,
                    score=0.5,
                    record={"chunk_id": "chunk_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                )
            ]

    case = DenseEvalCase(
        case_id="case_1",
        topic="smoke",
        query="机器人如何在烟雾中找人？",
        gold_parent_ids=["parent_gold"],
    )

    report = evaluate_dense_retriever(FakeRetriever(), [case], top_k=10)

    assert report.hit_at_1 == 1.0
    assert report.results[0].top_hits[0].chunk_id == "chunk_gold"
```

- [ ] **Step 10: Run tests to verify conversion tests fail**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_runner_red tests/test_rag_dense_eval.py -q
```

Expected:

```text
ImportError: cannot import name 'evaluate_dense_retriever'
```

- [ ] **Step 11: Implement conversion and retriever runner**

Append to `src/fireclaw_core/rag/dense_eval.py`:

```python
def hits_from_dense_results(hits: list[DenseHit]) -> list[DenseEvalRetrievedHit]:
    converted: list[DenseEvalRetrievedHit] = []
    for hit in hits:
        record = hit.record
        text = str(record.get("clean_text") or record.get("text") or "")
        converted.append(
            DenseEvalRetrievedHit(
                rank=hit.rank,
                score=hit.score,
                chunk_id=str(record.get("chunk_id") or ""),
                parent_id=str(record.get("parent_id") or ""),
                doc_id=str(record.get("doc_id") or ""),
                source_file=str(record.get("source_file") or ""),
                page_start=record.get("page_start"),
                page_end=record.get("page_end"),
                heading=str(record.get("heading") or ""),
                text_preview=text[:240],
            )
        )
    return converted


def evaluate_dense_retriever(
    retriever: Any,
    cases: list[DenseEvalCase],
    *,
    top_k: int = 10,
) -> DenseEvalReport:
    hits_by_case_id: dict[str, list[DenseEvalRetrievedHit]] = {}
    for case in cases:
        hits = retriever.query(case.query, top_k=top_k)
        hits_by_case_id[case.case_id] = hits_from_dense_results(hits)
    return evaluate_ranked_hits(cases, hits_by_case_id, top_k=top_k)
```

- [ ] **Step 12: Run all dense eval unit tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_core_final tests/test_rag_dense_eval.py -q
```

Expected:

```text
8 passed
```

- [ ] **Step 13: Checkpoint without commit**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## rag-dev...origin/rag-dev
?? src/fireclaw_core/rag/dense_eval.py
?? tests/test_rag_dense_eval.py
```

The existing untracked spec and memory files may also appear.

---

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

### Task 3: Ten Evidence-First Gold Cases

**Files:**
- Create: `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`

**Interfaces:**
- Consumes:
  - `data/rag/fire_rescue/chunks/parent_chunks.jsonl`
  - `data/rag/fire_rescue/indexes/dense/bge-m3/records.jsonl`
  - `load_dense_eval_cases(path: Path) -> list[DenseEvalCase]`
- Produces:
  - 10 valid JSONL rows for the v1 Chinese dense eval gold set.

- [ ] **Step 1: Inspect candidate evidence by topic**

Use read-only searches over parent chunks and index records. Start with topic terms that align with the spec:

```powershell
rg -n -i "thermal|victim|smoke|visibility|USAR|rubble|mobility|gas|odor|rehabilitation|confined|hazardous|Smokeview|FDS|sprinkler|collapse" data\rag\fire_rescue\chunks\parent_chunks.jsonl
```

Expected:

```text
Several candidate parent chunk lines with parent_id, doc_id, page metadata, and text.
```

- [ ] **Step 2: Select 10 parent chunks**

Select parents that satisfy all of these conditions:

```text
1. parent_id is present.
2. text is substantive, not cover/front matter/table of contents.
3. evidence supports a FireClaw task, safety, sensing, or robot capability question.
4. topics are diverse.
5. source metadata remains traceable.
```

Record each selected parent with:

```text
case_id
topic
Chinese query
gold_parent_ids
source_doc_id
expected_evidence_summary
notes
```

- [ ] **Step 3: Create the JSONL file**

Create `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl` with exactly 10 rows. Each row must contain these fields with concrete values from Step 2: `case_id`, `topic`, `query`, `gold_parent_ids`, `gold_chunk_ids`, `expected_evidence_summary`, `source_doc_id`, and `notes`.

Before completing the step, run this structural check:

```powershell
$env:PYTHONPATH = ".deps;src"; python -c "from pathlib import Path; from fireclaw_core.rag.dense_eval import load_dense_eval_cases; cases=load_dense_eval_cases(Path('data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl')); assert len(cases)==10; assert all(c.case_id and c.query and c.gold_parent_ids for c in cases); print([c.case_id for c in cases])"
```

Expected:

```text
The command prints 10 case ids and exits with code 0.
```

- [ ] **Step 4: Validate the JSONL file with the loader**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -c "from pathlib import Path; from fireclaw_core.rag.dense_eval import load_dense_eval_cases; cases=load_dense_eval_cases(Path('data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl')); print(len(cases)); assert len(cases)==10"
```

Expected:

```text
10
```

- [ ] **Step 5: Run unit tests after adding data file**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_cases_final tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py -q
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
?? data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl
```

Other modified and untracked evaluation files may also appear.

---

### Task 4: Real BGE-M3 Evaluation Smoke Run

**Files:**
- Generate: `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`
- Modify: `memory/2026-07-07/fireclaw-dense-index-status-check.md`

**Interfaces:**
- Consumes:
  - `eval-dense-index` CLI command
  - `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`
  - existing BGE-M3 index at `data/rag/fire_rescue/indexes/dense/bge-m3`
  - local model at `.cache/models/bge-m3`
- Produces:
  - real evaluation report JSON
  - memory update with commands and observed metrics.

- [ ] **Step 1: Run all unit tests before manual smoke**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_unit_final tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Expected:

```text
All tests pass
```

If pytest hits the known sandbox temp directory permission issue, rerun the same command with approved elevated execution and a new `--basetemp`.

- [ ] **Step 2: Run real BGE-M3 dense evaluation**

Run:

```powershell
$env:PYTHONPATH = "src"; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Expected:

```text
JSON output containing case_count, hit_at_1, hit_at_5, hit_at_10, mrr_at_10, gold_recall_at_10, and per-case results.
```

Non-fatal Transformers tokenizer/cache warnings are acceptable if the command exits with code `0`.

- [ ] **Step 3: Inspect the generated report**

Run:

```powershell
Get-Content -Raw -Encoding UTF8 -LiteralPath data\rag\fire_rescue\eval\runs\dense_bge_m3_zh_v1_report.json
```

Expected:

```text
Valid JSON with 10 results.
```

Read the metrics carefully before making any quality claim. A low score is a valid result and should be reported plainly.

- [ ] **Step 4: Update memory**

Append to `memory/2026-07-07/fireclaw-dense-index-status-check.md`:

```markdown
## Dense Evaluation Harness Update

Implemented evidence-first dense retrieval evaluation for 10 Chinese task-style cases.

Commands:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_unit_final tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
$env:PYTHONPATH = "src"; .\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Observed metrics:

- Copy the exact `case_count`, `hit_at_1`, `hit_at_5`, `hit_at_10`, `mrr_at_10`, and `gold_recall_at_10` values from `data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json`.

Current conclusion:

- State whether the BGE-M3 dense index retrieved the selected gold parents well, poorly, or unevenly. Base the statement only on the observed metrics and per-case misses.

Next recommended step:

- State the next concrete retrieval-quality step based on the report, such as revising gold queries, adding Chinese sources, adding BM25, or introducing reranking.
```

- [ ] **Step 5: Final verification**

Run:

```powershell
git status --short --branch
```

Expected:

```text
Modified and untracked files only from the dense evaluation work, plus existing memory/spec files.
```

Do not claim the evaluation is complete until the unit tests and real smoke command have both been run and inspected.
