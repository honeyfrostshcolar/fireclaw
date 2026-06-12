from __future__ import annotations

import json

import pytest

from fireclaw_core.provider.model_catalog import (
    ModelCatalog,
    ModelDescriptor,
    ModelNotFoundError,
)


# --- helpers ---

def _write_config(path, models=None, default_model=None):
    """Write a minimal model catalog JSON config."""
    payload: dict = {
        "models": models or [
            {
                "id": "deepseek-chat",
                "name": "DeepSeek V3",
                "provider": "deepseek",
                "context_window": 128000,
                "max_tokens": 4096,
                "supports_tools": True,
                "cost_input": 0.5,
                "cost_output": 1.5,
            },
            {
                "id": "qwen-turbo",
                "name": "Qwen Turbo",
                "provider": "qwen",
                "context_window": 32000,
                "max_tokens": 2048,
                "supports_tools": False,
                "cost_input": 0.1,
                "cost_output": 0.2,
            },
        ],
    }
    if default_model is not None:
        payload["default_model"] = default_model
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# --- tests ---


def test_model_descriptor_fields():
    """ModelDescriptor is a frozen dataclass with all required fields."""
    desc = ModelDescriptor(
        id="deepseek-chat",
        name="DeepSeek V3",
        provider="deepseek",
        context_window=128000,
        max_tokens=4096,
        supports_tools=True,
        cost_input=0.5,
        cost_output=1.5,
    )
    assert desc.id == "deepseek-chat"
    assert desc.name == "DeepSeek V3"
    assert desc.provider == "deepseek"
    assert desc.context_window == 128000
    assert desc.max_tokens == 4096
    assert desc.supports_tools is True
    assert desc.cost_input == 0.5
    assert desc.cost_output == 1.5

    # Frozen — mutation must raise
    with pytest.raises(AttributeError):
        desc.id = "other"  # type: ignore[misc]


def test_model_catalog_loads_from_json(tmp_path):
    """Catalog loads two models from a JSON config file."""
    config = tmp_path / "models.json"
    _write_config(config)
    catalog = ModelCatalog(config)
    models = catalog.list_models()
    assert len(models) == 2
    ids = {m.id for m in models}
    assert ids == {"deepseek-chat", "qwen-turbo"}


def test_model_catalog_resolve_by_id(tmp_path):
    """Resolve returns the correct ModelDescriptor for a known id."""
    config = tmp_path / "models.json"
    _write_config(config)
    catalog = ModelCatalog(config)
    desc = catalog.resolve("qwen-turbo")
    assert desc.id == "qwen-turbo"
    assert desc.name == "Qwen Turbo"
    assert desc.context_window == 32000
    assert desc.supports_tools is False


def test_model_catalog_resolve_raises_on_missing(tmp_path):
    """Resolve raises ModelNotFoundError for an unknown id."""
    config = tmp_path / "models.json"
    _write_config(config)
    catalog = ModelCatalog(config)
    with pytest.raises(ModelNotFoundError) as exc_info:
        catalog.resolve("nonexistent-model")
    assert exc_info.value.model_id == "nonexistent-model"


def test_model_catalog_default_model(tmp_path):
    """default_model_id is read from the config's 'default_model' field."""
    config = tmp_path / "models.json"
    _write_config(config, default_model="deepseek-chat")
    catalog = ModelCatalog(config)
    assert catalog.default_model_id == "deepseek-chat"


def test_model_catalog_optional_cost(tmp_path):
    """cost_input and cost_output are optional (None when absent)."""
    models = [
        {
            "id": "free-model",
            "name": "Free Model",
            "provider": "local",
            "context_window": 8192,
            "max_tokens": 1024,
            "supports_tools": False,
        },
    ]
    config = tmp_path / "models.json"
    _write_config(config, models=models)
    catalog = ModelCatalog(config)
    desc = catalog.resolve("free-model")
    assert desc.cost_input is None
    assert desc.cost_output is None
