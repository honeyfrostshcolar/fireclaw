from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from typing import Any

import numpy as np

from fireclaw_core.rag.dense_retrieval import EmbeddingModelInfo


class BGEM3EmbeddingProvider:
    def __init__(
        self,
        model_path: Path,
        *,
        device: str | None = None,
        batch_size: int = 32,
        max_length: int = 8192,
        use_fp16: bool | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.use_fp16 = use_fp16
        self.model_info = EmbeddingModelInfo(
            provider="bge-m3",
            model=str(self.model_path),
            dimension=1024,
            normalized=True,
            backend="FlagEmbedding.BGEM3FlagModel",
            device=device or "auto",
            pooling="bge-m3-dense",
            max_length=max_length,
        )
        self._model: Any | None = None

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        model = self._load_model()
        result = model.encode(texts, batch_size=self.batch_size, max_length=self.max_length)
        dense_vectors = result["dense_vecs"] if isinstance(result, dict) else result
        return np.asarray(dense_vectors, dtype=np.float32)

    def _load_model(self) -> Any:
        if self._model is None:
            if not self.model_path.exists():
                raise FileNotFoundError(f"BGE-M3 model path not found: {self.model_path}")
            cache_dir = _huggingface_cache_dir_for(self.model_path)
            _set_huggingface_cache_env(cache_dir)
            try:
                import torch
                from FlagEmbedding import BGEM3FlagModel
            except ImportError as exc:
                raise ImportError(
                    "FlagEmbedding and torch are required for --provider bge-m3. "
                    "Use .\\.venv-bge-m3\\Scripts\\python.exe."
                ) from exc

            if self.device is not None:
                devices = [self.device]
            else:
                devices = ["cuda:0"] if torch.cuda.is_available() else ["cpu"]

            use_fp16 = self.use_fp16
            if use_fp16 is None:
                use_fp16 = bool(devices and devices[0].startswith("cuda"))

            self.model_info = replace(self.model_info, device=",".join(devices))
            kwargs: dict[str, Any] = {
                "use_fp16": use_fp16,
                "devices": devices,
                "batch_size": self.batch_size,
                "return_dense": True,
                "return_sparse": False,
                "return_colbert_vecs": False,
            }
            kwargs["cache_dir"] = str(cache_dir)
            self._model = BGEM3FlagModel(str(self.model_path), **kwargs)
        return self._model


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
