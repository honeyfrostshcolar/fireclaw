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
                "query": "鐑熼浘寰堝ぇ鐨勬埧闂撮噷濡備綍瀵绘壘琚洶浜哄憳锛?,
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
            query="鐑熼浘寰堝ぇ鐨勬埧闂撮噷濡備綍瀵绘壘琚洶浜哄憳锛?,
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
            query="鐑熼浘涓浣曠敤鐑垚鍍忔壘浜猴紵",
            gold_parent_ids=["parent_gold"],
        ),
        DenseEvalCase(
            case_id="case_2",
            topic="mobility",
            query="鏈哄櫒浜哄浣曢€氳繃纰庣煶鍦板舰锛?,
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
            query="搴熷鎼滄晳鏈哄櫒浜洪渶瑕佸摢浜涜兘鍔涳紵",
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
        query="鐑熼浘涓浣曠敤鐑垚鍍忔壘浜猴紵",
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
            assert query == "鏈哄櫒浜哄浣曞湪鐑熼浘涓壘浜猴紵"
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
        query="鏈哄櫒浜哄浣曞湪鐑熼浘涓壘浜猴紵",
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
