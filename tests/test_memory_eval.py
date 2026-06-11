"""Tests for memory retrieval evaluation harness."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fireclaw_core.memory_eval import (
    EvalCase,
    EvalReport,
    EvalResult,
    evaluate_retrieval,
    load_eval_cases,
)
from fireclaw_core.memory_retrieval import MemoryRetriever, RetrievedMemory


# ---------------------------------------------------------------------------
# Fake index for testing — avoids real SQLite
# ---------------------------------------------------------------------------


class FakeMemoryIndex:
    """Minimal in-memory index for evaluation tests."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        # Simple keyword match against content values.
        results = []
        for r in self._records:
            content_str = str(r.get("content", {}))
            if any(word in content_str for word in query.split()):
                results.append(r)
        return results[:limit]

    def get_embedding(self, record_id: str) -> list[float] | None:
        return None


def _make_retriever(records: list[dict[str, Any]]) -> MemoryRetriever:
    """Build a MemoryRetriever backed by a FakeMemoryIndex."""
    index = FakeMemoryIndex(records)
    return MemoryRetriever(index=index)


# ---------------------------------------------------------------------------
# EvalCase dataclass tests
# ---------------------------------------------------------------------------


class TestEvalCase:
    def test_creation(self) -> None:
        case = EvalCase(query="test", must_match=["a", "b"])
        assert case.query == "test"
        assert case.must_match == ["a", "b"]
        assert case.record_type is None

    def test_creation_with_record_type(self) -> None:
        case = EvalCase(query="q", must_match=[], record_type="outcome")
        assert case.record_type == "outcome"

    def test_frozen(self) -> None:
        case = EvalCase(query="q", must_match=[])
        with pytest.raises(AttributeError):
            case.query = "changed"  # type: ignore[misc]


class TestEvalResult:
    def test_creation(self) -> None:
        result = EvalResult(
            query="q", passed=True, matched_ids=["r1"],
            missing_patterns=[], hit_count=3,
        )
        assert result.passed is True
        assert result.hit_count == 3


class TestEvalReport:
    def test_meets_threshold_when_above(self) -> None:
        report = EvalReport(total=10, passed=8, failed=2, hit_rate=0.8)
        assert report.meets_threshold(0.5) is True
        assert report.meets_threshold(0.8) is True

    def test_meets_threshold_when_below(self) -> None:
        report = EvalReport(total=10, passed=3, failed=7, hit_rate=0.3)
        assert report.meets_threshold(0.5) is False

    def test_meets_threshold_exact_boundary(self) -> None:
        report = EvalReport(total=10, passed=5, failed=5, hit_rate=0.5)
        assert report.meets_threshold(0.5) is True
        assert report.meets_threshold(0.51) is False

    def test_meets_threshold_zero_threshold(self) -> None:
        report = EvalReport(total=0, passed=0, failed=0, hit_rate=0.0)
        assert report.meets_threshold(0.0) is True

    def test_meets_threshold_in_to_dict(self) -> None:
        """meets_threshold result is not in to_dict (it's a method, not data)."""
        report = EvalReport(total=2, passed=1, failed=1, hit_rate=0.5)
        d = report.to_dict()
        assert "meets_threshold" not in d

    def test_to_dict(self) -> None:
        report = EvalReport(
            total=2, passed=1, failed=1, hit_rate=0.5,
            results=[
                EvalResult(
                    query="q1", passed=True, matched_ids=["r1"],
                    missing_patterns=[], hit_count=1,
                ),
                EvalResult(
                    query="q2", passed=False, matched_ids=[],
                    missing_patterns=["missing"], hit_count=0,
                ),
            ],
            missing_cases=["bad case"],
        )
        d = report.to_dict()
        assert d["total"] == 2
        assert d["passed"] == 1
        assert d["failed"] == 1
        assert d["hit_rate"] == 0.5
        assert len(d["results"]) == 2
        assert d["results"][0]["passed"] is True
        assert d["results"][1]["missing_patterns"] == ["missing"]
        assert d["missing_cases"] == ["bad case"]


# ---------------------------------------------------------------------------
# evaluate_retrieval tests
# ---------------------------------------------------------------------------


class TestEvaluateRetrieval:
    def test_passes_when_all_patterns_match(self) -> None:
        records = [
            {"record_id": "r1", "mission_id": "m1", "record_type": "outcome",
             "content": {"command": "去二楼搜索", "status": "succeeded"},
             "created_at": ""},
        ]
        retriever = _make_retriever(records)
        cases = [{"query": "二楼 搜索", "must_match": ["二楼"]}]

        report = evaluate_retrieval(retriever, cases)

        assert report.total == 1
        assert report.passed == 1
        assert report.hit_rate == 1.0

    def test_fails_when_pattern_missing(self) -> None:
        records = [
            {"record_id": "r1", "mission_id": "m1", "record_type": "outcome",
             "content": {"command": "去三楼巡逻"}, "created_at": ""},
        ]
        retriever = _make_retriever(records)
        cases = [{"query": "二楼", "must_match": ["二楼"]}]

        report = evaluate_retrieval(retriever, cases)

        assert report.total == 1
        assert report.passed == 0
        assert report.hit_rate == 0.0
        assert report.results[0].missing_patterns == ["二楼"]

    def test_handles_empty_cases(self) -> None:
        retriever = _make_retriever([])
        report = evaluate_retrieval(retriever, [])

        assert report.total == 0
        assert report.hit_rate == 0.0

    def test_handles_invalid_case_format(self) -> None:
        retriever = _make_retriever([])
        cases = [{"invalid_key": True}]

        report = evaluate_retrieval(retriever, cases)

        assert len(report.missing_cases) == 1

    def test_report_to_dict_roundtrip(self) -> None:
        records = [
            {"record_id": "r1", "mission_id": "m1", "record_type": "outcome",
             "content": {"command": "搜索"}, "created_at": ""},
        ]
        retriever = _make_retriever(records)
        cases = [{"query": "搜索", "must_match": ["搜索"]}]

        report = evaluate_retrieval(retriever, cases)
        d = report.to_dict()

        assert d["total"] == 1
        assert d["passed"] == 1
        assert isinstance(d["results"], list)

    def test_pattern_matches_against_record_type(self) -> None:
        """Patterns can match against record_type as well as content."""
        records = [
            {"record_id": "r1", "mission_id": "m1", "record_type": "outcome",
             "content": {"status": "ok"}, "created_at": ""},
        ]
        retriever = _make_retriever(records)
        cases = [{"query": "ok", "must_match": ["outcome"]}]

        report = evaluate_retrieval(retriever, cases)

        assert report.total == 1
        assert report.passed == 1

    def test_multiple_cases_mixed_results(self) -> None:
        records = [
            {"record_id": "r1", "mission_id": "m1", "record_type": "outcome",
             "content": {"command": "灭火"}, "created_at": ""},
        ]
        retriever = _make_retriever(records)
        cases = [
            {"query": "灭火", "must_match": ["灭火"]},
            {"query": "巡逻", "must_match": ["巡逻"]},
        ]

        report = evaluate_retrieval(retriever, cases)

        assert report.total == 2
        assert report.passed == 1
        assert report.failed == 1
        assert report.hit_rate == 0.5

    def test_expected_record_ids_and_min_score_are_enforced(self) -> None:
        records = [
            {"record_id": "successful-rescue-floor-2", "mission_id": "m1", "record_type": "mission_outcome",
             "content": {"command": "去二楼救人", "status": "succeeded"}, "created_at": ""},
            {"record_id": "wrong-record", "mission_id": "m2", "record_type": "mission_outcome",
             "content": {"command": "去二楼救人", "status": "failed"}, "created_at": ""},
        ]
        retriever = _make_retriever(records)
        cases = [{
            "query": "去二楼救人",
            "expected_record_ids": ["successful-rescue-floor-2"],
            "min_score": 0.4,
            "record_type": "mission_outcome",
        }]

        report = evaluate_retrieval(retriever, cases)

        assert report.total == 1
        assert report.passed == 1
        assert report.results[0].matched_ids == ["successful-rescue-floor-2"]

    def test_record_type_mismatch_fails_expected_record_case(self) -> None:
        records = [
            {"record_id": "successful-rescue-floor-2", "mission_id": "m1", "record_type": "operator_correction",
             "content": {"command": "去二楼救人", "status": "succeeded"}, "created_at": ""},
        ]
        retriever = _make_retriever(records)
        cases = [{
            "query": "去二楼救人",
            "expected_record_ids": ["successful-rescue-floor-2"],
            "record_type": "mission_outcome",
        }]

        report = evaluate_retrieval(retriever, cases)

        assert report.total == 1
        assert report.passed == 0
        assert "successful-rescue-floor-2" in report.results[0].missing_patterns


# ---------------------------------------------------------------------------
# load_eval_cases tests
# ---------------------------------------------------------------------------


class TestLoadEvalCases:
    def test_loads_from_json_file(self, tmp_path: Path) -> None:
        cases = [{"query": "test", "must_match": ["test"]}]
        path = tmp_path / "cases.json"
        path.write_text(json.dumps(cases), encoding="utf-8")

        loaded = load_eval_cases(path)

        assert len(loaded) == 1
        assert loaded[0]["query"] == "test"

    def test_loads_fixture_file(self) -> None:
        """The bundled fixture file loads without error."""
        fixture_path = Path(__file__).parent / "fixtures" / "memory_eval_cases.json"
        assert fixture_path.exists(), f"Fixture not found at {fixture_path}"
        loaded = load_eval_cases(fixture_path)
        assert len(loaded) == 3
        for case in loaded:
            assert "query" in case
            assert "must_match" in case

    def test_rescue_fixture_loads_correctly(self) -> None:
        """The rescue-query demo-gate fixture has the expected structure."""
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "memory_eval"
            / "fireclaw_rescue_queries.json"
        )
        assert fixture_path.exists(), f"Fixture not found at {fixture_path}"
        cases = load_eval_cases(fixture_path)
        assert len(cases) == 2
        assert cases[0]["query"] == "去二楼救人"
        assert cases[0]["expected_record_ids"] == ["successful-rescue-floor-2"]
        assert cases[0]["min_score"] == 0.4
        assert cases[1]["query"] == "热成像误报"
        assert cases[1]["expected_record_ids"] == ["thermal-false-positive-correction"]
        assert cases[1]["min_score"] == 0.35


# ---------------------------------------------------------------------------
# MemoryRetriever.status() tests
# ---------------------------------------------------------------------------


class TestMemoryRetrieverStatus:
    def test_status_without_embedding_provider(self) -> None:
        index = FakeMemoryIndex([])
        retriever = MemoryRetriever(index=index)

        status = retriever.status()

        assert status["lexical_index_available"] is True
        assert status["embedding_provider_configured"] is False
        assert status["embedding_provider_available"] is None
        assert status["lexical_weight"] == 0.6
        assert status["embedding_weight"] == 0.4

    def test_status_with_embedding_provider(self) -> None:
        class FakeProvider:
            def embed(self, text: str) -> list[float]:
                return [0.1, 0.2]

            @property
            def dimensions(self) -> int:
                return 2

        index = FakeMemoryIndex([])
        retriever = MemoryRetriever(index=index, embedding_provider=FakeProvider())

        status = retriever.status()

        assert status["embedding_provider_configured"] is True
        assert status["embedding_provider_available"] is True
        assert status["lexical_weight"] == 0.6
        assert status["embedding_weight"] == 0.4

    def test_status_with_custom_weights(self) -> None:
        index = FakeMemoryIndex([])
        retriever = MemoryRetriever(
            index=index, lexical_weight=0.8, embedding_weight=0.2,
        )

        status = retriever.status()

        assert status["lexical_weight"] == 0.8
        assert status["embedding_weight"] == 0.2

    def test_status_with_broken_embedding_provider(self) -> None:
        """When the embedding provider raises on dimensions, report unavailable."""
        class BrokenProvider:
            def embed(self, text: str) -> list[float]:
                return [0.1]

            @property
            def dimensions(self) -> int:
                raise RuntimeError("provider offline")

        index = FakeMemoryIndex([])
        retriever = MemoryRetriever(index=index, embedding_provider=BrokenProvider())

        status = retriever.status()

        assert status["embedding_provider_configured"] is True
        assert status["embedding_provider_available"] is False
