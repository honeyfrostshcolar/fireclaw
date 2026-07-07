from __future__ import annotations

from fireclaw_core.rag.dense_eval import DenseEvalCase
from fireclaw_core.rag.dense_retrieval import DenseHit
from fireclaw_core.rag.query_expansion import QueryExpansion


def test_evaluate_bm25_retriever_with_expansion_uses_reviewed_en_and_terms() -> None:
    from fireclaw_core.rag.dense_eval import evaluate_bm25_retriever_with_expansion

    seen_queries: list[str] = []

    class FakeBM25Retriever:
        def query(self, query: str, *, top_k: int = 5) -> list[DenseHit]:
            seen_queries.append(query)
            if "SCBA" in query or "rehabilitation" in query:
                return [
                    DenseHit(
                        rank=1,
                        score=3.0,
                        record={"chunk_id": "chunk_gold", "parent_id": "parent_gold", "doc_id": "doc"},
                    )
                ]
            return []

    case = DenseEvalCase(
        case_id="dense_zh_008",
        topic="rehab",
        query="消防员什么时候进入rehab？",
        gold_parent_ids=["parent_gold"],
    )
    expansions = {
        "dense_zh_008": QueryExpansion(
            case_id="dense_zh_008",
            query_zh=case.query,
            llm_query_en="When should firefighters enter rehab?",
            reviewed_query_en="When must firefighters enter rehabilitation after SCBA use?",
            term_query="SCBA rehabilitation medical evaluation hydration",
            terms=["SCBA", "rehabilitation", "medical evaluation", "hydration"],
            status="reviewed",
        )
    }

    report = evaluate_bm25_retriever_with_expansion(
        FakeBM25Retriever(),
        [case],
        query_expansions=expansions,
        query_variants=["en", "terms"],
        ranking_view="parent",
        small_top_k=10,
        top_k=10,
        require_reviewed_expansions=True,
        query_expansions_path="query_expansions.jsonl",
    )

    assert seen_queries == [
        "When must firefighters enter rehabilitation after SCBA use?",
        "SCBA rehabilitation medical evaluation hydration",
    ]
    assert report.hit_at_1 == 1.0
    assert report.retrieval_config is not None
    assert report.retrieval_config["retrieval_method"] == "bm25"
    assert report.retrieval_config["query_variants"] == ["en", "terms"]
    assert report.retrieval_config["require_reviewed_expansions"] is True
