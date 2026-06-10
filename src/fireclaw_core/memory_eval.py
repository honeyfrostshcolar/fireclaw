"""Memory retrieval evaluation harness for FireClaw.

Provides ``evaluate_retrieval()`` for running regression tests against the
memory retrieval subsystem.  Uses evaluation fixtures (query/must_match pairs)
to verify that relevant memories are found.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fireclaw_core.memory_retrieval import MemoryRetriever, RetrievedMemory


@dataclass(frozen=True)
class EvalCase:
    """A single evaluation case."""

    query: str
    must_match: list[str]
    record_type: str | None = None


@dataclass
class EvalResult:
    """Result of evaluating a single case."""

    query: str
    passed: bool
    matched_ids: list[str]
    missing_patterns: list[str]
    hit_count: int


@dataclass
class EvalReport:
    """Aggregated evaluation report."""

    total: int
    passed: int
    failed: int
    hit_rate: float
    results: list[EvalResult] = field(default_factory=list)
    missing_cases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "hit_rate": round(self.hit_rate, 3),
            "results": [
                {
                    "query": r.query,
                    "passed": r.passed,
                    "matched_ids": r.matched_ids,
                    "missing_patterns": r.missing_patterns,
                    "hit_count": r.hit_count,
                }
                for r in self.results
            ],
            "missing_cases": self.missing_cases,
        }


def evaluate_retrieval(
    retriever: MemoryRetriever,
    cases: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> EvalReport:
    """Evaluate retrieval quality against a set of test cases.

    Each case has:
    - query: the search query
    - must_match: list of patterns that must appear in returned content
    - record_type: optional expected record type (matched against returned records)

    Returns an ``EvalReport`` with hit rate, per-case results, and missing cases.
    """
    results: list[EvalResult] = []
    missing: list[str] = []

    for i, case_data in enumerate(cases):
        try:
            case = EvalCase(
                query=case_data["query"],
                must_match=case_data.get("must_match", []),
                record_type=case_data.get("record_type"),
            )
        except (KeyError, TypeError) as exc:
            missing.append(f"Case {i}: invalid format ({exc})")
            continue

        retrieved = retriever.retrieve(case.query, limit=limit)

        # Check which patterns match.
        matched_ids: list[str] = []
        missing_patterns: list[str] = []

        for pattern in case.must_match:
            found = False
            for mem in retrieved:
                content_str = str(mem.content).lower()
                if (
                    pattern.lower() in content_str
                    or pattern.lower() in mem.record_type.lower()
                ):
                    found = True
                    matched_ids.append(mem.record_id)
                    break
            if not found:
                missing_patterns.append(pattern)

        passed = len(missing_patterns) == 0
        results.append(
            EvalResult(
                query=case.query,
                passed=passed,
                matched_ids=list(set(matched_ids)),
                missing_patterns=missing_patterns,
                hit_count=len(retrieved),
            )
        )

    total = len(results)
    passed_count = sum(1 for r in results if r.passed)
    hit_rate = passed_count / total if total > 0 else 0.0

    return EvalReport(
        total=total,
        passed=passed_count,
        failed=total - passed_count,
        hit_rate=hit_rate,
        results=results,
        missing_cases=missing,
    )


def load_eval_cases(path: Path) -> list[dict[str, Any]]:
    """Load evaluation cases from a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))
