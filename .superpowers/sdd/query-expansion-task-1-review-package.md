# Review package: Query Expansion Task 1

## Scoped status
```text
?? src/fireclaw_core/rag/query_expansion.py
?? tests/test_rag_query_expansion.py
```

## File: src\fireclaw_core\rag\query_expansion.py
```python
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


SUPPORTED_QUERY_VARIANTS = {"zh", "en", "terms"}


@dataclass(frozen=True)
class QueryExpansion:
    case_id: str
    query_zh: str
    llm_query_en: str = ""
    reviewed_query_en: str = ""
    term_query: str = ""
    terms: list[str] = field(default_factory=list)
    status: str = "candidate"
    notes: str = ""

    @property
    def effective_query_en(self) -> str:
        reviewed = self.reviewed_query_en.strip()
        if reviewed:
            return reviewed
        return self.llm_query_en.strip()


@dataclass(frozen=True)
class QueryVariant:
    name: str
    text: str


def load_query_expansions(path: Path) -> dict[str, QueryExpansion]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Query expansion JSONL not found: {path}")

    expansions: dict[str, QueryExpansion] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc

            case_id = str(data.get("case_id") or "").strip()
            if not case_id:
                raise ValueError(f"Missing case_id in {path}:{line_no}")
            if case_id in expansions:
                raise ValueError(f"duplicate query expansion case_id: {case_id}")

            expansions[case_id] = QueryExpansion(
                case_id=case_id,
                query_zh=str(data.get("query_zh") or ""),
                llm_query_en=str(data.get("llm_query_en") or ""),
                reviewed_query_en=str(data.get("reviewed_query_en") or ""),
                term_query=str(data.get("term_query") or ""),
                terms=_coerce_str_list(data.get("terms")),
                status=str(data.get("status") or "candidate"),
                notes=str(data.get("notes") or ""),
            )

    return expansions


def build_query_variants(
    case: Any,
    expansions: Mapping[str, QueryExpansion],
    requested_variants: Sequence[str],
    *,
    require_reviewed: bool = False,
) -> list[QueryVariant]:
    variants: list[QueryVariant] = []
    for variant_name in requested_variants:
        if variant_name not in SUPPORTED_QUERY_VARIANTS:
            raise ValueError(f"Unsupported query variant: {variant_name}")
        if variant_name == "zh":
            variants.append(QueryVariant(name="zh", text=str(case.query)))
            continue

        expansion = expansions.get(str(case.case_id))
        if expansion is None:
            raise ValueError(f"missing query expansion for case_id: {case.case_id}")
        if require_reviewed and expansion.status != "reviewed":
            raise ValueError(f"case_id {case.case_id} requires reviewed query expansion")

        if variant_name == "en":
            text = expansion.effective_query_en
        else:
            text = expansion.term_query.strip()
        if not text:
            raise ValueError(f"missing {variant_name} query variant for case_id: {case.case_id}")
        variants.append(QueryVariant(name=variant_name, text=text))
    return variants


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Expected a JSON list")
    return [str(item).strip() for item in value if str(item).strip()]
```

## File: tests\test_rag_query_expansion.py
```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.query_expansion import QueryExpansion
from fireclaw_core.rag.query_expansion import build_query_variants
from fireclaw_core.rag.query_expansion import load_query_expansions


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_load_query_expansions_prefers_reviewed_translation(tmp_path: Path) -> None:
    path = tmp_path / "query_expansions.jsonl"
    _write_jsonl(
        path,
        [
            {
                "case_id": "dense_zh_008",
                "query_zh": "消防员什么时候进入rehab？",
                "llm_query_en": "When should firefighters enter rehab?",
                "reviewed_query_en": "When must firefighters enter formal rehabilitation?",
                "term_query": "SCBA NFPA 1584 formal rehab",
                "terms": ["SCBA", "NFPA 1584", "formal rehab"],
                "status": "candidate",
                "notes": "Candidate reviewed wording.",
            }
        ],
    )

    expansions = load_query_expansions(path)

    expansion = expansions["dense_zh_008"]
    assert isinstance(expansion, QueryExpansion)
    assert expansion.effective_query_en == "When must firefighters enter formal rehabilitation?"
    assert expansion.term_query == "SCBA NFPA 1584 formal rehab"
    assert expansion.terms == ["SCBA", "NFPA 1584", "formal rehab"]


def test_load_query_expansions_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    path = tmp_path / "query_expansions.jsonl"
    _write_jsonl(
        path,
        [
            {"case_id": "dup", "query_zh": "q1", "llm_query_en": "en1"},
            {"case_id": "dup", "query_zh": "q2", "llm_query_en": "en2"},
        ],
    )

    with pytest.raises(ValueError, match="duplicate query expansion case_id: dup"):
        load_query_expansions(path)


def test_build_query_variants_returns_requested_order() -> None:
    case = DenseEvalCase(
        case_id="dense_zh_002",
        topic="gas",
        query="已知厂房平面图时，机器人如何规划远程气体扫描？",
        gold_parent_ids=["parent_gold"],
    )
    expansions = {
        "dense_zh_002": QueryExpansion(
            case_id="dense_zh_002",
            query_zh=case.query,
            llm_query_en="How should a robot plan remote gas scanning in a known factory map?",
            reviewed_query_en="",
            term_query="remote gas detection TDLAS methane leak coverage planning",
            terms=["remote gas detection", "TDLAS", "methane leak", "coverage planning"],
            status="candidate",
            notes="Candidate terms.",
        )
    }

    variants = build_query_variants(case, expansions, ["zh", "en", "terms"])

    assert [(variant.name, variant.text) for variant in variants] == [
        ("zh", case.query),
        ("en", "How should a robot plan remote gas scanning in a known factory map?"),
        ("terms", "remote gas detection TDLAS methane leak coverage planning"),
    ]


def test_build_query_variants_requires_reviewed_status_when_requested() -> None:
    case = DenseEvalCase(
        case_id="dense_zh_009",
        topic="hazmat",
        query="USAR进入危险品污染现场前检查什么？",
        gold_parent_ids=["parent_gold"],
    )
    expansions = {
        "dense_zh_009": QueryExpansion(
            case_id="dense_zh_009",
            query_zh=case.query,
            llm_query_en="What should USAR teams check before entering a hazmat site?",
            reviewed_query_en="",
            term_query="hazmat PPE go/no-go decontamination",
            terms=["hazmat", "PPE", "go/no-go", "decontamination"],
            status="candidate",
            notes="Needs user review.",
        )
    }

    with pytest.raises(ValueError, match="requires reviewed query expansion"):
        build_query_variants(case, expansions, ["zh", "en"], require_reviewed=True)


def test_build_query_variants_rejects_missing_requested_variant() -> None:
    case = DenseEvalCase(
        case_id="dense_zh_010",
        topic="thermal",
        query="如何规划热辐射更低的路线？",
        gold_parent_ids=["parent_gold"],
    )

    with pytest.raises(ValueError, match="missing query expansion for case_id: dense_zh_010"):
        build_query_variants(case, {}, ["zh", "en"])
```
