from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import os
from pathlib import Path
import re
from typing import Protocol

from fireclaw_core.rag.dense_eval import DenseEvalRetrievedHit
from fireclaw_core.rag.dense_retrieval import load_jsonl


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
