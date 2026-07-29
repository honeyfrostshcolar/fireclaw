from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ModelNotFoundError(Exception):
    """Raised when a model id is not present in the catalog."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(f"Model not found in catalog: {model_id}")


@dataclass(frozen=True)
class ModelDescriptor:
    """Immutable metadata for a single LLM model."""

    id: str
    name: str
    provider: str
    context_window: int
    max_tokens: int
    supports_tools: bool
    cost_input: float | None = None
    cost_output: float | None = None
    tokenizer_id: str | None = None


class ModelCatalog:
    """Loads model metadata from a JSON config file and provides lookup."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._models: dict[str, ModelDescriptor] = {}
        self.default_model_id: str | None = None

        if config_path is not None:
            self._load(Path(config_path))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, model_id: str) -> ModelDescriptor:
        """Return the descriptor for *model_id* or raise ``ModelNotFoundError``."""
        try:
            return self._models[model_id]
        except KeyError:
            raise ModelNotFoundError(model_id) from None

    def list_models(self) -> list[ModelDescriptor]:
        """Return all registered model descriptors."""
        return list(self._models.values())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)

        for entry in data.get("models", []):
            descriptor = _descriptor_from_dict(entry)
            self._models[descriptor.id] = descriptor

        default = data.get("default_model")
        if isinstance(default, str):
            self.default_model_id = default


def _descriptor_from_dict(data: dict[str, Any]) -> ModelDescriptor:
    return ModelDescriptor(
        id=str(data["id"]),
        name=str(data["name"]),
        provider=str(data["provider"]),
        context_window=int(data["context_window"]),
        max_tokens=int(data["max_tokens"]),
        supports_tools=bool(data["supports_tools"]),
        cost_input=float(data["cost_input"]) if "cost_input" in data else None,
        cost_output=float(data["cost_output"]) if "cost_output" in data else None,
        tokenizer_id=(
            str(data["tokenizer_id"])
            if data.get("tokenizer_id")
            else None
        ),
    )
