from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import os
from pathlib import Path
import re
from typing import Protocol

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_retrieval import load_jsonl
from fireclaw_core.rag.query_expansion import SUPPORTED_QUERY_VARIANTS


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class RerankerModelInfo:
    provider: str
    model: str
    backend: str
    device: str | None = None
    batch_size: int = 32
    max_length: int = 512

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RerankerProvider(Protocol):
    model_info: RerankerModelInfo

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        raise NotImplementedError


class FakeRerankerProvider:
    def __init__(self) -> None:
        self.model_info = RerankerModelInfo(
            provider="fake-reranker",
            model="fake-token-overlap-v1",
            backend="deterministic-token-overlap",
            batch_size=32,
            max_length=512,
        )

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        scores: list[float] = []
        for query, passage in pairs:
            query_tokens = _token_set(query)
            passage_tokens = _token_set(passage)
            if not query_tokens:
                scores.append(0.0)
                continue
            overlap = query_tokens & passage_tokens
            scores.append(len(overlap) / len(query_tokens))
        return scores


class BGEFlagRerankerProvider:
    def __init__(
        self,
        model_path: Path,
        *,
        device: str | None = None,
        batch_size: int = 32,
        max_length: int = 512,
        use_fp16: bool | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.use_fp16 = use_fp16
        self.model_info = RerankerModelInfo(
            provider="bge-reranker",
            model=str(self.model_path),
            backend="FlagEmbedding.FlagReranker",
            device=device or "auto",
            batch_size=batch_size,
            max_length=max_length,
        )
        self._model: object | None = None

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        model = self._load_model()
        raw_scores = model.compute_score(list(pairs), batch_size=self.batch_size, max_length=self.max_length)
        if isinstance(raw_scores, float):
            return [float(raw_scores)]
        return [float(score) for score in raw_scores]

    def _load_model(self):
        if self._model is None:
            if not self.model_path.exists():
                raise FileNotFoundError(f"BGE reranker model path not found: {self.model_path}")
            cache_dir = _huggingface_cache_dir_for(self.model_path)
            _set_huggingface_cache_env(cache_dir)
            try:
                import torch
                from FlagEmbedding import FlagReranker
            except ImportError as exc:
                raise ImportError(
                    "FlagEmbedding and torch are required for --reranker-provider bge-reranker. "
                    "Use .\\.venv-bge-m3\\Scripts\\python.exe."
                ) from exc

            if self.device is not None:
                devices: str | list[str] = [self.device]
            else:
                devices = ["cuda:0"] if torch.cuda.is_available() else ["cpu"]
            use_fp16 = self.use_fp16
            if use_fp16 is None:
                use_fp16 = bool(devices and str(devices[0]).startswith("cuda"))
            self.model_info = replace(self.model_info, device=",".join(str(item) for item in devices))
            self._model = FlagReranker(
                str(self.model_path),
                use_fp16=use_fp16,
                devices=devices,
                batch_size=self.batch_size,
                max_length=self.max_length,
                cache_dir=str(cache_dir),
            )
        return self._model


def load_parent_texts(path: Path) -> dict[str, str]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Parent chunks JSONL not found: {path}")
    texts: dict[str, str] = {}
    for row in load_jsonl(path):
        parent_id = str(row.get("parent_id") or "").strip()
        if not parent_id:
            raise ValueError(f"missing parent_id in parent chunks: {path}")
        if parent_id in texts:
            raise ValueError(f"duplicate parent_id in parent chunks: {parent_id}")
        if "text" not in row:
            raise ValueError(f"missing text in parent chunks for parent_id: {parent_id}")
        text = str(row.get("text") or "")
        if not text.strip():
            raise ValueError(f"empty text in parent chunks for parent_id: {parent_id}")
        texts[parent_id] = text
    return texts


def load_small_texts(path: Path) -> dict[str, str]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Small chunks JSONL not found: {path}")
    texts: dict[str, str] = {}
    for row in load_jsonl(path):
        chunk_id = str(row.get("chunk_id") or "").strip()
        if not chunk_id:
            raise ValueError(f"missing chunk_id in small chunks: {path}")
        if chunk_id in texts:
            raise ValueError(f"duplicate chunk_id in small chunks: {chunk_id}")
        if "clean_text" in row:
            text = str(row.get("clean_text") or "")
        elif "text" in row:
            text = str(row.get("text") or "")
        else:
            raise ValueError(f"missing text in small chunks for chunk_id: {chunk_id}")
        if not text.strip():
            raise ValueError(f"empty text in small chunks for chunk_id: {chunk_id}")
        texts[chunk_id] = text
    return texts


def select_rerank_query(
    query_variants: Mapping[str, str],
    fallback_query: str,
    variant: str = "en",
) -> str:
    preferred_keys = [f"dense:{variant}", f"bm25:{variant}", variant]
    for key in preferred_keys:
        text = str(query_variants.get(key) or "").strip()
        if text:
            return text
    return fallback_query


def rerank_parent_hits(
    query: str,
    hits: list[DenseEvalRetrievedHit],
    parent_texts: Mapping[str, str],
    reranker: RerankerProvider,
    *,
    top_k: int = 10,
    max_passage_chars: int = 6000,
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if max_passage_chars <= 0:
        raise ValueError("max_passage_chars must be positive")

    pairs: list[tuple[str, str]] = []
    for hit in hits:
        parent_text = parent_texts.get(hit.parent_id)
        if parent_text is None:
            raise ValueError(f"missing parent text for parent_id: {hit.parent_id}")
        pairs.append((query, parent_text[:max_passage_chars]))

    scores = reranker.score_pairs(pairs)
    if len(scores) != len(hits):
        raise ValueError(f"reranker returned {len(scores)} scores for {len(hits)} hits")

    scored = list(zip(hits, scores, strict=True))
    scored.sort(key=lambda item: (-float(item[1]), item[0].rank, item[0].parent_id))
    reranked: list[DenseEvalRetrievedHit] = []
    for new_rank, (hit, score) in enumerate(scored[:top_k], start=1):
        reranked.append(
            replace(
                hit,
                rank=new_rank,
                score=float(score),
                rerank_score=float(score),
                base_rank=hit.rank,
                base_score=hit.score,
                base_fusion_score=hit.fusion_score,
                reranker=reranker.model_info.provider,
            )
        )
    return reranked


def rerank_small_hits(
    query: str,
    hits: list[DenseEvalRetrievedHit],
    small_texts: Mapping[str, str],
    reranker: RerankerProvider,
    *,
    top_k: int,
    max_passage_chars: int = 2000,
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if max_passage_chars <= 0:
        raise ValueError("max_passage_chars must be positive")

    pairs: list[tuple[str, str]] = []
    for hit in hits:
        small_text = small_texts.get(hit.chunk_id)
        if small_text is None:
            raise ValueError(f"missing small text for chunk_id: {hit.chunk_id}")
        pairs.append((query, small_text[:max_passage_chars]))

    scores = reranker.score_pairs(pairs)
    if len(scores) != len(hits):
        raise ValueError(f"reranker returned {len(scores)} scores for {len(hits)} hits")

    scored = list(zip(hits, scores, strict=True))
    scored.sort(key=lambda item: (-float(item[1]), item[0].rank, item[0].parent_id, item[0].chunk_id))
    reranked: list[DenseEvalRetrievedHit] = []
    for new_rank, (hit, score) in enumerate(scored[:top_k], start=1):
        reranked.append(
            replace(
                hit,
                rank=new_rank,
                score=float(score),
                rerank_score=float(score),
                base_rank=hit.rank,
                base_score=hit.score,
                base_fusion_score=hit.fusion_score,
                reranker=reranker.model_info.provider,
            )
        )
    return reranked


def select_rerank_queries(
    query_variants: Mapping[str, str],
    fallback_query: str,
    variants: Sequence[str],
) -> dict[str, str]:
    selected: dict[str, str] = {}
    for variant in variants:
        name = str(variant).strip()
        if not name:
            raise ValueError("rerank query variants must not contain empty values")
        if name not in SUPPORTED_QUERY_VARIANTS:
            raise ValueError(f"Unsupported rerank query variant: {name}")
        if name in selected:
            raise ValueError(f"duplicate rerank query variant: {name}")
        selected[name] = select_rerank_query(query_variants, fallback_query, variant=name)
    if not selected:
        raise ValueError("at least one rerank query variant is required")
    return selected


def rerank_parent_hits_with_rrf(
    queries_by_variant: Mapping[str, str],
    hits: list[DenseEvalRetrievedHit],
    parent_texts: Mapping[str, str],
    reranker: RerankerProvider,
    *,
    top_k: int = 10,
    rrf_k: int = 60,
    max_passage_chars: int = 6000,
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    if max_passage_chars <= 0:
        raise ValueError("max_passage_chars must be positive")

    selected_queries = {
        str(variant).strip(): str(query).strip()
        for variant, query in queries_by_variant.items()
        if str(variant).strip() and str(query).strip()
    }
    if not selected_queries:
        raise ValueError("at least one non-empty rerank query is required")

    parent_passages: dict[str, str] = {}
    for hit in hits:
        parent_text = parent_texts.get(hit.parent_id)
        if parent_text is None:
            raise ValueError(f"missing parent text for parent_id: {hit.parent_id}")
        parent_passages[hit.parent_id] = parent_text[:max_passage_chars]

    rrf_scores: dict[str, float] = {hit.parent_id: 0.0 for hit in hits}
    per_variant_ranks: dict[str, dict[str, int]] = {hit.parent_id: {} for hit in hits}
    per_variant_scores: dict[str, dict[str, float]] = {hit.parent_id: {} for hit in hits}

    for variant, query in selected_queries.items():
        pairs = [(query, parent_passages[hit.parent_id]) for hit in hits]
        scores = reranker.score_pairs(pairs)
        if len(scores) != len(hits):
            raise ValueError(f"reranker returned {len(scores)} scores for {len(hits)} hits")

        scored = list(zip(hits, scores, strict=True))
        scored.sort(key=lambda item: (-float(item[1]), item[0].rank, item[0].parent_id))
        rank_key = f"rerank:{variant}"
        for rank, (hit, score) in enumerate(scored, start=1):
            rrf_scores[hit.parent_id] += 1.0 / (rrf_k + rank)
            per_variant_ranks[hit.parent_id][rank_key] = rank
            per_variant_scores[hit.parent_id][rank_key] = float(score)

    ranked_hits = sorted(
        hits,
        key=lambda hit: (-rrf_scores[hit.parent_id], hit.rank, hit.parent_id),
    )

    reranked: list[DenseEvalRetrievedHit] = []
    for new_rank, hit in enumerate(ranked_hits[:top_k], start=1):
        parent_id = hit.parent_id
        reranked.append(
            replace(
                hit,
                rank=new_rank,
                score=rrf_scores[parent_id],
                rerank_score=rrf_scores[parent_id],
                base_rank=hit.rank,
                base_score=hit.score,
                base_fusion_score=hit.fusion_score,
                variant_ranks={**hit.variant_ranks, **per_variant_ranks[parent_id]},
                variant_scores={**hit.variant_scores, **per_variant_scores[parent_id]},
                reranker=f"{reranker.model_info.provider}:rrf",
            )
        )
    return reranked


def rerank_small_hits_with_rrf(
    queries_by_variant: Mapping[str, str],
    hits: list[DenseEvalRetrievedHit],
    small_texts: Mapping[str, str],
    reranker: RerankerProvider,
    *,
    top_k: int,
    rrf_k: int = 60,
    max_passage_chars: int = 2000,
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    if max_passage_chars <= 0:
        raise ValueError("max_passage_chars must be positive")

    selected_queries = {
        str(variant).strip(): str(query).strip()
        for variant, query in queries_by_variant.items()
        if str(variant).strip() and str(query).strip()
    }
    if not selected_queries:
        raise ValueError("at least one non-empty rerank query is required")

    small_passages: dict[str, str] = {}
    for hit in hits:
        small_text = small_texts.get(hit.chunk_id)
        if small_text is None:
            raise ValueError(f"missing small text for chunk_id: {hit.chunk_id}")
        small_passages[hit.chunk_id] = small_text[:max_passage_chars]

    rrf_scores: dict[str, float] = {hit.chunk_id: 0.0 for hit in hits}
    per_variant_ranks: dict[str, dict[str, int]] = {hit.chunk_id: {} for hit in hits}
    per_variant_scores: dict[str, dict[str, float]] = {hit.chunk_id: {} for hit in hits}

    for variant, query in selected_queries.items():
        pairs = [(query, small_passages[hit.chunk_id]) for hit in hits]
        scores = reranker.score_pairs(pairs)
        if len(scores) != len(hits):
            raise ValueError(f"reranker returned {len(scores)} scores for {len(hits)} hits")

        scored = list(zip(hits, scores, strict=True))
        scored.sort(key=lambda item: (-float(item[1]), item[0].rank, item[0].parent_id, item[0].chunk_id))
        rank_key = f"rerank:{variant}"
        for rank, (hit, score) in enumerate(scored, start=1):
            rrf_scores[hit.chunk_id] += 1.0 / (rrf_k + rank)
            per_variant_ranks[hit.chunk_id][rank_key] = rank
            per_variant_scores[hit.chunk_id][rank_key] = float(score)

    ranked_hits = sorted(
        hits,
        key=lambda hit: (-rrf_scores[hit.chunk_id], hit.rank, hit.parent_id, hit.chunk_id),
    )

    reranked: list[DenseEvalRetrievedHit] = []
    for new_rank, hit in enumerate(ranked_hits[:top_k], start=1):
        chunk_id = hit.chunk_id
        reranked.append(
            replace(
                hit,
                rank=new_rank,
                score=rrf_scores[chunk_id],
                rerank_score=rrf_scores[chunk_id],
                base_rank=hit.rank,
                base_score=hit.score,
                base_fusion_score=hit.fusion_score,
                variant_ranks={**hit.variant_ranks, **per_variant_ranks[chunk_id]},
                variant_scores={**hit.variant_scores, **per_variant_scores[chunk_id]},
                reranker=f"{reranker.model_info.provider}:rrf",
            )
        )
    return reranked


def fuse_hybrid_and_rerank_hits(
    hybrid_hits: list[DenseEvalRetrievedHit],
    reranked_hits: list[DenseEvalRetrievedHit],
    *,
    top_k: int = 10,
    rrf_k: int = 60,
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")

    hybrid_by_parent = _first_hit_by_parent_rank(hybrid_hits)
    reranked_by_parent = _first_hit_by_parent_rank(reranked_hits)
    parent_ids = sorted(
        hybrid_by_parent.keys() & reranked_by_parent.keys(),
        key=lambda parent_id: (
            -(
                (1.0 / (rrf_k + hybrid_by_parent[parent_id].rank))
                + (1.0 / (rrf_k + reranked_by_parent[parent_id].rank))
            ),
            hybrid_by_parent[parent_id].rank,
            reranked_by_parent[parent_id].rank,
            parent_id,
        ),
    )

    fused: list[DenseEvalRetrievedHit] = []
    for rank, parent_id in enumerate(parent_ids[:top_k], start=1):
        hybrid_hit = hybrid_by_parent[parent_id]
        reranked_hit = reranked_by_parent[parent_id]
        joint_score = (1.0 / (rrf_k + hybrid_hit.rank)) + (1.0 / (rrf_k + reranked_hit.rank))
        rerank_score = _reranked_score(reranked_hit)
        fused.append(
            replace(
                reranked_hit,
                rank=rank,
                score=joint_score,
                fusion_score=joint_score,
                variant_ranks={
                    **hybrid_hit.variant_ranks,
                    **reranked_hit.variant_ranks,
                    "hybrid": hybrid_hit.rank,
                    "rerank": reranked_hit.rank,
                },
                variant_scores={
                    **hybrid_hit.variant_scores,
                    **reranked_hit.variant_scores,
                    "hybrid": hybrid_hit.score,
                    "rerank": rerank_score,
                },
            )
        )
    return fused


def aggregate_reranked_small_hits_by_parent(
    hits: list[DenseEvalRetrievedHit],
    *,
    top_k: int,
) -> list[DenseEvalRetrievedHit]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    grouped: dict[str, list[DenseEvalRetrievedHit]] = {}
    for hit in sorted(hits, key=lambda item: item.rank):
        grouped.setdefault(hit.parent_id, []).append(hit)

    representatives: list[DenseEvalRetrievedHit] = []
    for parent_hits in grouped.values():
        best = max(parent_hits, key=lambda item: (_reranked_score(item), -item.rank))
        representatives.append(
            replace(
                best,
                child_hit_count=len(parent_hits),
                child_ranks=[hit.rank for hit in parent_hits],
            )
        )

    representatives.sort(key=lambda item: (-_reranked_score(item), item.rank, item.parent_id))
    return [replace(hit, rank=rank) for rank, hit in enumerate(representatives[:top_k], start=1)]


def _reranked_score(hit: DenseEvalRetrievedHit) -> float:
    return float(hit.rerank_score if hit.rerank_score is not None else hit.score)


def _first_hit_by_parent_rank(hits: list[DenseEvalRetrievedHit]) -> dict[str, DenseEvalRetrievedHit]:
    by_parent: dict[str, DenseEvalRetrievedHit] = {}
    for hit in sorted(hits, key=lambda item: (item.rank, item.parent_id)):
        by_parent.setdefault(hit.parent_id, hit)
    return by_parent


def _token_set(text: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)}


def _huggingface_cache_dir_for(model_path: Path) -> Path:
    model_path = Path(model_path)
    if len(model_path.parents) >= 2:
        return model_path.parents[1] / "huggingface"
    return Path(".cache") / "huggingface"


def _set_huggingface_cache_env(cache_dir: Path) -> None:
    cache_dir = Path(cache_dir)
    transformers_dir = cache_dir / "transformers"
    hub_dir = cache_dir / "hub"
    transformers_dir.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_dir))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(transformers_dir))
    os.environ.setdefault("HF_HUB_CACHE", str(hub_dir))
