"""Tests for log_redaction module."""
from __future__ import annotations

from fireclaw_core.infra.log_redaction import redact_dict, redact_secrets


class TestRedactSecrets:
    def test_redacts_api_key(self) -> None:
        text = "Using key sk-abc12345defghij for auth"
        result = redact_secrets(text)
        assert "sk-abc12345defghij" not in result
        assert "sk-***" in result

    def test_redacts_bearer_token(self) -> None:
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.signature"
        result = redact_secrets(text)
        assert "eyJhbGciOiJIUzI1NiJ9.signature" not in result
        assert "Bearer ***" in result

    def test_redacts_api_key_equals(self) -> None:
        text = "api_key=supersecretkey12345"
        result = redact_secrets(text)
        assert "supersecretkey12345" not in result
        assert "api_key=***" in result

    def test_redacts_api_key_colon(self) -> None:
        text = "api_key: supersecretkey12345"
        result = redact_secrets(text)
        assert "supersecretkey12345" not in result
        assert "api_key=***" in result

    def test_redacts_password_equals(self) -> None:
        text = "password=hunter2stuff"
        result = redact_secrets(text)
        assert "hunter2stuff" not in result
        assert "password=***" in result

    def test_redacts_token_equals(self) -> None:
        text = "token=mytokenvalue12345"
        result = redact_secrets(text)
        assert "mytokenvalue12345" not in result
        assert "token=***" in result

    def test_preserves_non_secret_text(self) -> None:
        text = "This is a normal log message with no secrets"
        assert redact_secrets(text) == text

    def test_handles_multiple_secrets(self) -> None:
        text = "key sk-abcdef1234567890 and password=mypass1234"
        result = redact_secrets(text)
        assert "sk-abcdef1234567890" not in result
        assert "mypass1234" not in result
        assert "sk-***" in result
        assert "password=***" in result


class TestRedactDict:
    def test_redacts_string_values(self) -> None:
        data = {"api_key": "sk-abcdef1234567890", "name": "test"}
        result = redact_dict(data)
        assert result["api_key"] == "sk-***"
        assert result["name"] == "test"

    def test_redacts_nested_dicts(self) -> None:
        data = {
            "config": {
                "token": "token=mytokenvalue12345",
                "debug": True,
            },
            "count": 5,
        }
        result = redact_dict(data)
        assert "mytokenvalue12345" not in result["config"]["token"]
        assert result["config"]["token"] == "token=***"
        assert result["config"]["debug"] is True
        assert result["count"] == 5

    def test_handles_list_values(self) -> None:
        data = {"items": ["sk-abcdef1234567890", "normal"], "ok": True}
        result = redact_dict(data)
        assert "sk-abcdef1234567890" not in result["items"][0]
        assert result["items"][1] == "normal"

    def test_preserves_non_string_leaf_values(self) -> None:
        data = {"num": 42, "flag": True, "nothing": None}
        result = redact_dict(data)
        assert result == {"num": 42, "flag": True, "nothing": None}
