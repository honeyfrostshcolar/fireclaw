"""FireClaw Secret Isolation and Credentials Storage Engine."""
from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Optional

from fireclaw_core.infra.runtime_paths import resolve_fireclaw_runtime_root


class SecretManager:
    """Manages secure 0600 isolated credentials storage and profile secret sanitization."""

    SECRET_KEY_NAMES = {
        "api_key",
        "apikey",
        "secret",
        "secret_key",
        "password",
        "passwd",
        "token",
        "auth_token",
        "access_token",
        "private_key",
        "credential",
        "credentials",
    }

    def __init__(self, credentials_file: Optional[Path] = None) -> None:
        if credentials_file is not None:
            self.credentials_file = Path(credentials_file).resolve()
        else:
            runtime_root = resolve_fireclaw_runtime_root()
            self.credentials_file = runtime_root / "credentials.json"

        # Ensure directory exists with 0700 permissions if accessible
        try:
            self.credentials_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.credentials_file.parent, 0o700)
        except OSError:
            pass

        # If credentials file already exists, enforce 0600
        try:
            if self.credentials_file.exists():
                os.chmod(self.credentials_file, 0o600)
        except OSError:
            pass

    def get_secret(self, key_name: str, env_fallback: bool = True) -> Optional[str]:
        """Get secret value. Checks environment variables first (if env_fallback=True), then credentials.json."""
        if env_fallback:
            val = os.environ.get(key_name)
            if val is not None and val != "":
                return val

        try:
            if not self.credentials_file.exists():
                return None
            data = json.loads(self.credentials_file.read_text(encoding="utf-8"))
            return data.get(key_name)
        except Exception:
            return None

    def set_secret(self, key_name: str, secret_value: str) -> None:
        """Store or update a secret key-value pair in credentials.json with 0600 permissions."""
        data: dict[str, str] = {}
        try:
            if self.credentials_file.exists():
                data = json.loads(self.credentials_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
        except Exception:
            data = {}

        data[key_name] = secret_value
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

        # Atomic write with 0600 permissions
        parent = self.credentials_file.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=".tmp_cred_")
        temp_path = Path(tmp_name)
        try:
            with open(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp_path, 0o600)
            temp_path.replace(self.credentials_file)
            os.chmod(self.credentials_file, 0o600)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def delete_secret(self, key_name: str) -> bool:
        """Remove a secret from credentials.json."""
        try:
            if not self.credentials_file.exists():
                return False
            data = json.loads(self.credentials_file.read_text(encoding="utf-8"))
            if key_name in data:
                del data[key_name]
                content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
                self._atomic_write_0600(content)
                return True
        except Exception:
            pass
        return False

    def mask_secret(self, secret_value: str) -> str:
        """Mask a secret string for safe display (e.g. sk-****xyz)."""
        if not secret_value:
            return ""

        length = len(secret_value)
        if length <= 6:
            return "***"

        if secret_value.startswith("sk-") and length > 8:
            prefix = secret_value[:4]
            suffix = secret_value[-4:]
            return f"{prefix}***{suffix}"

        if length > 12:
            return f"{secret_value[:3]}***{secret_value[-3:]}"

        return f"{secret_value[:2]}***{secret_value[-2:]}"

    def sanitize_profile_dict(self, profile_dict: dict[str, Any]) -> dict[str, Any]:
        """Strip raw API keys/passwords from profile dictionary, replacing with *_env references."""
        result = copy.deepcopy(profile_dict)

        def _sanitize_node(node: Any, parent_key: str = "") -> Any:
            if isinstance(node, dict):
                sanitized_dict: dict[str, Any] = {}
                for k, v in node.items():
                    k_lower = k.lower()
                    if k_lower in self.SECRET_KEY_NAMES and isinstance(v, str):
                        # Convert to env reference
                        env_key_name = f"{k}_env"
                        env_var_name = f"{parent_key.upper()}_{k.upper()}" if parent_key else k.upper()
                        # If the dictionary already has an env reference, do not overwrite it
                        if env_key_name not in node:
                            sanitized_dict[env_key_name] = env_var_name
                    elif isinstance(v, (dict, list)):
                        sanitized_dict[k] = _sanitize_node(v, k)
                    else:
                        sanitized_dict[k] = v
                return sanitized_dict
            elif isinstance(node, list):
                return [_sanitize_node(item, parent_key) for item in node]
            return node

        return _sanitize_node(result)

    def _atomic_write_0600(self, content: bytes) -> None:
        parent = self.credentials_file.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=".tmp_cred_")
        temp_path = Path(tmp_name)
        try:
            with open(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp_path, 0o600)
            temp_path.replace(self.credentials_file)
            os.chmod(self.credentials_file, 0o600)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
