from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from fireclaw_core.rag.relevance_eval import (
    RelevanceJudgment,
    load_relevance_judgments,
    ndcg_at_k,
    relevant_parent_ids,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_load_relevance_judgments_accepts_valid_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "judgments.jsonl"
    _write_jsonl(
        path,
        [
            {"case_id": "case_1", "parent_id": "parent_a", "grade": 3, "source": "review", "notes": "best"},
            {"case_id": "case_1", "parent_id": "parent_b", "grade": 1},
            {"case_id": "case_2", "parent_id": "parent_c", "grade": 2},
        ],
    )

    judgments = load_relevance_judgments(path)

    assert judgments["case_1"]["parent_a"] == RelevanceJudgment(
        case_id="case_1",
        parent_id="parent_a",
        grade=3,
        source="review",
        notes="best",
    )
    assert judgments["case_1"]["parent_b"].grade == 1
    assert judgments["case_2"]["parent_c"].grade == 2


def test_load_relevance_judgments_rejects_duplicate_case_parent_pair(tmp_path: Path) -> None:
    path = tmp_path / "judgments.jsonl"
    _write_jsonl(
        path,
        [
            {"case_id": "case_1", "parent_id": "parent_a", "grade": 2},
            {"case_id": "case_1", "parent_id": "parent_a", "grade": 3},
        ],
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_relevance_judgments(path)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"parent_id": "parent_a", "grade": 2}, "case_id"),
        ({"case_id": "case_1", "grade": 2}, "parent_id"),
        ({"case_id": "case_1", "parent_id": "parent_a", "grade": "high"}, "grade"),
        ({"case_id": "case_1", "parent_id": "parent_a", "grade": -1}, "grade"),
        ({"case_id": "case_1", "parent_id": "parent_a", "grade": 4}, "grade"),
        ({"case_id": "case_1", "parent_id": "parent_a", "grade": True}, "grade"),
    ],
)
def test_load_relevance_judgments_rejects_invalid_rows(
    tmp_path: Path,
    row: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / "judgments.jsonl"
    _write_jsonl(path, [row])

    with pytest.raises(ValueError, match=message):
        load_relevance_judgments(path)


def test_relevant_parent_ids_filters_by_threshold() -> None:
    judgments = {
        "parent_a": RelevanceJudgment(case_id="case_1", parent_id="parent_a", grade=3),
        "parent_b": RelevanceJudgment(case_id="case_1", parent_id="parent_b", grade=2),
        "parent_c": RelevanceJudgment(case_id="case_1", parent_id="parent_c", grade=1),
    }

    assert relevant_parent_ids(judgments) == ["parent_a", "parent_b"]
    assert relevant_parent_ids(judgments, threshold=3) == ["parent_a"]


def test_ndcg_at_k_uses_graded_gain() -> None:
    judgments = {
        "parent_a": RelevanceJudgment(case_id="case_1", parent_id="parent_a", grade=3),
        "parent_b": RelevanceJudgment(case_id="case_1", parent_id="parent_b", grade=2),
        "parent_c": RelevanceJudgment(case_id="case_1", parent_id="parent_c", grade=1),
    }

    score = ndcg_at_k(["parent_b", "parent_x", "parent_a"], judgments, k=3)

    dcg = ((2**2) - 1) / math.log2(2) + 0.0 + ((2**3) - 1) / math.log2(4)
    idcg = ((2**3) - 1) / math.log2(2) + ((2**2) - 1) / math.log2(3) + ((2**1) - 1) / math.log2(4)
    assert score == pytest.approx(dcg / idcg)


def test_ndcg_at_k_treats_duplicate_parent_ids_as_one_ranked_parent() -> None:
    judgments = {
        "parent_high": RelevanceJudgment(case_id="case_1", parent_id="parent_high", grade=3),
        "parent_mid": RelevanceJudgment(case_id="case_1", parent_id="parent_mid", grade=2),
    }

    score = ndcg_at_k(
        ["parent_high", "parent_high", "parent_high", "parent_mid"],
        judgments,
        k=4,
    )

    assert score <= 1.0
    assert score == pytest.approx(1.0)
