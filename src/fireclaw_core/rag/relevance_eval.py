from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class RelevanceJudgment:
    case_id: str
    parent_id: str
    grade: int
    source: str = ""
    notes: str = ""


def load_relevance_judgments(path: Path) -> dict[str, dict[str, RelevanceJudgment]]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Relevance judgment file not found: {path}")

    judgments_by_case: dict[str, dict[str, RelevanceJudgment]] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc

            case_id = str(row.get("case_id") or "").strip()
            parent_id = str(row.get("parent_id") or "").strip()
            grade = row.get("grade")

            if not case_id:
                raise ValueError(f"Missing case_id in {path}:{line_no}")
            if not parent_id:
                raise ValueError(f"Missing parent_id in {path}:{line_no}")
            if not isinstance(grade, int) or isinstance(grade, bool):
                raise ValueError(f"Invalid grade in {path}:{line_no}: {grade!r}")
            if grade < 0 or grade > 3:
                raise ValueError(f"grade must be within 0..3 in {path}:{line_no}: {grade}")

            case_judgments = judgments_by_case.setdefault(case_id, {})
            if parent_id in case_judgments:
                raise ValueError(f"duplicate relevance judgment for ({case_id}, {parent_id})")

            case_judgments[parent_id] = RelevanceJudgment(
                case_id=case_id,
                parent_id=parent_id,
                grade=grade,
                source=str(row.get("source") or ""),
                notes=str(row.get("notes") or ""),
            )

    return judgments_by_case


def relevant_parent_ids(judgments: Mapping[str, RelevanceJudgment], threshold: int = 2) -> list[str]:
    return [parent_id for parent_id, judgment in judgments.items() if judgment.grade >= threshold]


def ndcg_at_k(
    ranked_parent_ids: Sequence[str],
    judgments: Mapping[str, RelevanceJudgment],
    *,
    k: int = 10,
) -> float:
    if k <= 0 or not judgments:
        return 0.0

    ranked: list[str] = []
    seen_parent_ids: set[str] = set()
    for parent_id in ranked_parent_ids[:k]:
        if parent_id in seen_parent_ids:
            continue
        seen_parent_ids.add(parent_id)
        ranked.append(parent_id)

    dcg = 0.0
    for index, parent_id in enumerate(ranked, start=1):
        judgment = judgments.get(parent_id)
        if judgment is None or judgment.grade <= 0:
            continue
        dcg += ((2**judgment.grade) - 1) / math.log2(index + 1)

    ideal_grades = sorted((judgment.grade for judgment in judgments.values() if judgment.grade > 0), reverse=True)[:k]
    if not ideal_grades:
        return 0.0

    idcg = 0.0
    for index, grade in enumerate(ideal_grades, start=1):
        idcg += ((2**grade) - 1) / math.log2(index + 1)
    if idcg == 0.0:
        return 0.0
    return dcg / idcg
