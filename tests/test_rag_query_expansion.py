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
