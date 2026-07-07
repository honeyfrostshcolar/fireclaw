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
