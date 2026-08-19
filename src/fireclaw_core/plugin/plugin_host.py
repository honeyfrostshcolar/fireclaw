"""Unified plugin contribution host for FireClaw.

The host follows OpenClaw's plugin API/registry shape: one plugin identity is
given an injected registration API, all contributions are staged together,
and activation either commits as a unit or rolls back completely.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, ContextManager as AbstractContextManager, Iterable, Literal

from fireclaw_core.plugin.sdk_adapter import (
    normalize_registered_physical_capability,
    normalize_registered_tool,
)


PluginStatus = Literal["registered", "active", "failed", "disposed"]
PluginTrustLevel = Literal[
    "builtin",
    "trusted",
    "sandboxed",
    "descriptor_only",
]
ContributionKind = Literal[
    "tool",
    "physical_capability",
    "hook",
    "service",
    "context_engine",
    "agent_harness",
]


@dataclass(frozen=True)
class PluginDiagnostic:
    plugin_id: str
    phase: str
    code: str
    message: str
    contribution_kind: str | None = None
    contribution_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "phase": self.phase,
            "code": self.code,
            "message": self.message,
            "contribution_kind": self.contribution_kind,
            "contribution_id": self.contribution_id,
        }


@dataclass(frozen=True)
class PluginContribution:
    kind: ContributionKind
    contribution_id: str
    owner_plugin_id: str
    value: Any
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.contribution_id


@dataclass
class PluginRecord:
    plugin_id: str
    name: str
    version: str | None = None
    description: str | None = None
    source: str = "runtime"
    api_version: str = "1"
    trust_level: PluginTrustLevel = "descriptor_only"
    status: PluginStatus = "registered"
    contribution_keys: list[tuple[str, str]] = field(default_factory=list)
    diagnostics: list[PluginDiagnostic] = field(default_factory=list)
    activated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "source": self.source,
            "api_version": self.api_version,
            "trust_level": self.trust_level,
            "status": self.status,
            "contributions": [
                {"kind": kind, "contribution_id": contribution_id}
                for kind, contribution_id in self.contribution_keys
            ],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "activated_at": self.activated_at,
        }


class PluginRegistrationError(ValueError):
    def __init__(self, diagnostic: PluginDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


class PluginActivationTransaction(AbstractContextManager["FireClawPluginApi"]):
    def __init__(
        self,
        host: "FireClawPluginHost",
        *,
        plugin_id: str,
        name: str | None = None,
        version: str | None = None,
        description: str | None = None,
        source: str = "runtime",
        api_version: str = "1",
        trust_level: PluginTrustLevel,
    ) -> None:
        self._host = host
        self._record = PluginRecord(
            plugin_id=_required_id(plugin_id, "plugin_id"),
            name=(name or plugin_id).strip(),
            version=version,
            description=description,
            source=source,
            api_version=api_version,
            trust_level=_trust_level(trust_level),
        )
        self._staged: list[PluginContribution] = []
        self._dispose_callbacks: list[Callable[[], None]] = []
        self._settled = False
        self.api = FireClawPluginApi(self)

    @property
    def plugin_id(self) -> str:
        return self._record.plugin_id

    def stage(
        self,
        *,
        kind: ContributionKind,
        contribution_id: str,
        value: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._settled:
            raise RuntimeError("plugin registration transaction is already settled")
        self._staged.append(
            PluginContribution(
                kind=kind,
                contribution_id=_required_id(
                    contribution_id,
                    "contribution_id",
                ),
                owner_plugin_id=self.plugin_id,
                value=value,
                metadata=deepcopy(metadata or {}),
            )
        )

    def register_dispose(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("plugin dispose callback must be callable")
        self._dispose_callbacks.append(callback)

    def commit(self) -> PluginRecord:
        if self._settled:
            raise RuntimeError("plugin registration transaction is already settled")
        try:
            record = self._host._commit_registration(
                self._record,
                self._staged,
                self._dispose_callbacks,
            )
        except Exception:
            self._settled = True
            raise
        self._settled = True
        return record

    def rollback(self) -> None:
        self._settled = True
        self._staged.clear()
        self._dispose_callbacks.clear()

    def __enter__(self) -> "FireClawPluginApi":
        return self.api

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is not None:
            self.rollback()
            return False
        self.commit()
        return False


class FireClawPluginApi:
    """Plugin-scoped API. Ownership is injected and cannot be forged."""

    def __init__(self, transaction: PluginActivationTransaction) -> None:
        self._transaction = transaction

    @property
    def id(self) -> str:
        return self._transaction.plugin_id

    def register_tool(
        self,
        tool: Any,
        *,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        tool = normalize_registered_tool(tool)
        self._transaction.stage(
            kind="tool",
            contribution_id=name or _value_id(tool, "name"),
            value=tool,
            metadata=metadata,
        )

    def register_physical_capability(self, capability: Any) -> None:
        capability = normalize_registered_physical_capability(capability)
        declared_plugin_id = getattr(capability, "plugin_id", None)
        if declared_plugin_id is not None and declared_plugin_id != self.id:
            raise PluginRegistrationError(
                PluginDiagnostic(
                    plugin_id=self.id,
                    phase="register",
                    code="physical_tool_owner_mismatch",
                    message=(
                        "Physical Tool plugin_id must match the injected "
                        "Plugin API identity."
                    ),
                    contribution_kind="physical_capability",
                    contribution_id=str(getattr(capability, "name", "")) or None,
                )
            )
        self._transaction.stage(
            kind="physical_capability",
            contribution_id=_value_id(capability, "name"),
            value=capability,
        )

    def register_physical_tool(self, tool: Any) -> None:
        """Register a public SDK physical Tool contract."""
        self.register_physical_capability(tool)

    def register_hook(
        self,
        hook_type: str,
        hook_name: str,
        callback: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> None:
        self._transaction.stage(
            kind="hook",
            contribution_id=f"{self.id}:{hook_type}:{hook_name}",
            value=callback,
            metadata={"hook_type": hook_type, "hook_name": hook_name},
        )

    def register_service(
        self,
        service_id: str,
        service: Any,
        *,
        data_only: bool = False,
    ) -> None:
        self._transaction.stage(
            kind="service",
            contribution_id=service_id,
            value=service,
            metadata={"data_only": data_only},
        )

    def register_context_engine(self, engine_id: str, engine: Any) -> None:
        self._transaction.stage(
            kind="context_engine",
            contribution_id=engine_id,
            value=engine,
        )

    def register_agent_harness(self, harness: Any) -> None:
        self._transaction.stage(
            kind="agent_harness",
            contribution_id=_value_id(harness, "id"),
            value=harness,
        )

    def register_dispose(self, callback: Callable[[], None]) -> None:
        self._transaction.register_dispose(callback)


class FireClawPluginHost:
    """Single owner of FireClaw plugin records and contribution identities."""

    SUPPORTED_API_VERSIONS = frozenset({"1"})

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, PluginRecord] = {}
        self._contributions: dict[tuple[str, str], PluginContribution] = {}
        self._dispose_callbacks: dict[str, tuple[Callable[[], None], ...]] = {}

    def begin_registration(
        self,
        plugin_id: str,
        *,
        name: str | None = None,
        version: str | None = None,
        description: str | None = None,
        source: str = "runtime",
        api_version: str = "1",
        trust_level: PluginTrustLevel,
    ) -> PluginActivationTransaction:
        return PluginActivationTransaction(
            self,
            plugin_id=plugin_id,
            name=name,
            version=version,
            description=description,
            source=source,
            api_version=api_version,
            trust_level=trust_level,
        )

    def activate(
        self,
        plugin_id: str,
        register: Callable[[FireClawPluginApi], None],
        *,
        trust_level: PluginTrustLevel,
        **metadata: Any,
    ) -> PluginRecord:
        if trust_level == "descriptor_only":
            raise ValueError(
                "descriptor_only plugins must use register_data_service(); "
                "the generic activation callback is executable host code."
            )
        transaction = self.begin_registration(
            plugin_id,
            trust_level=trust_level,
            **metadata,
        )
        try:
            register(transaction.api)
            return transaction.commit()
        except Exception as exc:
            transaction.rollback()
            self._record_activation_failure(
                plugin_id,
                exc,
                {"trust_level": trust_level, **metadata},
            )
            raise

    def register_data_service(
        self,
        *,
        plugin_id: str,
        service_id: str,
        value: Any,
        name: str | None = None,
        version: str | None = None,
        description: str | None = None,
        source: str = "descriptor",
        api_version: str = "1",
    ) -> PluginRecord:
        """Register inert descriptor data without invoking plugin code."""

        transaction = self.begin_registration(
            plugin_id,
            name=name,
            version=version,
            description=description,
            source=source,
            api_version=api_version,
            trust_level="descriptor_only",
        )
        try:
            transaction.api.register_service(
                service_id,
                value,
                data_only=True,
            )
            return transaction.commit()
        except Exception as exc:
            transaction.rollback()
            self._record_activation_failure(
                plugin_id,
                exc,
                {
                    "name": name,
                    "version": version,
                    "description": description,
                    "source": source,
                    "api_version": api_version,
                    "trust_level": "descriptor_only",
                },
            )
            raise

    def register_contribution(
        self,
        *,
        plugin_id: str,
        kind: ContributionKind,
        contribution_id: str,
        value: Any,
        metadata: dict[str, Any] | None = None,
        source: str = "compatibility",
        trust_level: PluginTrustLevel,
    ) -> PluginRecord:
        def register(api: FireClawPluginApi) -> None:
            api._transaction.stage(
                kind=kind,
                contribution_id=contribution_id,
                value=value,
                metadata=metadata,
            )

        return self.activate(
            plugin_id,
            register,
            source=source,
            trust_level=trust_level,
        )

    def get(
        self,
        kind: ContributionKind,
        contribution_id: str,
    ) -> PluginContribution | None:
        with self._lock:
            return self._contributions.get((kind, contribution_id))

    def contributions(
        self,
        kind: ContributionKind | None = None,
    ) -> tuple[PluginContribution, ...]:
        with self._lock:
            values = tuple(self._contributions.values())
        if kind is None:
            return values
        return tuple(item for item in values if item.kind == kind)

    def values(self, kind: ContributionKind) -> tuple[Any, ...]:
        return tuple(item.value for item in self.contributions(kind))

    def record(self, plugin_id: str) -> PluginRecord | None:
        with self._lock:
            record = self._records.get(plugin_id)
            return deepcopy(record) if record is not None else None

    def records(self) -> tuple[PluginRecord, ...]:
        with self._lock:
            return tuple(
                deepcopy(record)
                for record in sorted(
                    self._records.values(),
                    key=lambda item: item.plugin_id,
                )
            )

    def inventory(self) -> dict[str, Any]:
        records = self.records()
        return {
            "api_versions": tuple(sorted(self.SUPPORTED_API_VERSIONS)),
            "plugins": tuple(record.to_dict() for record in records),
            "contributions": tuple(
                {
                    "kind": item.kind,
                    "contribution_id": item.contribution_id,
                    "owner_plugin_id": item.owner_plugin_id,
                    "metadata": deepcopy(item.metadata),
                }
                for item in sorted(
                    self.contributions(),
                    key=lambda value: value.key,
                )
            ),
        }

    def dispose_plugin(self, plugin_id: str) -> None:
        with self._lock:
            record = self._records.get(plugin_id)
            if record is None or record.status == "disposed":
                return
            callbacks = self._dispose_callbacks.pop(plugin_id, ())
            for key in record.contribution_keys:
                current = self._contributions.get(key)
                if current is not None and current.owner_plugin_id == plugin_id:
                    del self._contributions[key]
            record.status = "disposed"
        for callback in reversed(callbacks):
            try:
                callback()
            except Exception:
                diagnostic = PluginDiagnostic(
                    plugin_id=plugin_id,
                    phase="dispose",
                    code="dispose_failed",
                    message="Plugin dispose callback failed.",
                )
                with self._lock:
                    record.diagnostics.append(diagnostic)

    def dispose(self) -> None:
        for record in reversed(self.records()):
            self.dispose_plugin(record.plugin_id)

    def _commit_registration(
        self,
        record: PluginRecord,
        staged: Iterable[PluginContribution],
        dispose_callbacks: Iterable[Callable[[], None]],
    ) -> PluginRecord:
        staged_values = list(staged)
        dispose_values = list(dispose_callbacks)
        keys = [item.key for item in staged_values]
        if len(keys) != len(set(keys)):
            duplicate = next(key for key in keys if keys.count(key) > 1)
            raise self._registration_error(
                record.plugin_id,
                "duplicate_contribution",
                f"Plugin {record.plugin_id!r} registered {duplicate!r} more than once.",
                duplicate,
            )
        if record.api_version not in self.SUPPORTED_API_VERSIONS:
            raise self._registration_error(
                record.plugin_id,
                "unsupported_api_version",
                f"Unsupported FireClaw plugin API version: {record.api_version}",
            )
        if record.trust_level == "descriptor_only":
            if dispose_values:
                raise self._registration_error(
                    record.plugin_id,
                    "untrusted_plugin_executable_contribution",
                    "Descriptor-only plugins cannot register dispose callbacks.",
                )
            for item in staged_values:
                if (
                    item.kind != "service"
                    or item.metadata.get("data_only") is not True
                    or callable(item.value)
                ):
                    raise self._registration_error(
                        record.plugin_id,
                        "untrusted_plugin_executable_contribution",
                        (
                            "Descriptor-only plugins may register only "
                            "non-callable data services."
                        ),
                        item.key,
                    )
        if record.trust_level == "sandboxed":
            if dispose_values:
                raise self._registration_error(
                    record.plugin_id,
                    "sandboxed_plugin_host_callback_forbidden",
                    "Sandboxed plugins cannot register host dispose callbacks.",
                )
            for item in staged_values:
                if (
                    item.kind != "tool"
                    or item.metadata.get("execution_boundary")
                    != "docker_sandbox"
                ):
                    raise self._registration_error(
                        record.plugin_id,
                        "sandboxed_plugin_boundary_invalid",
                        (
                            "Sandboxed plugins may contribute only Tool "
                            "wrappers bound to the Docker execution boundary."
                        ),
                        item.key,
                    )
        with self._lock:
            current_record = self._records.get(record.plugin_id)
            if current_record is not None and current_record.status not in {
                "active",
                "disposed",
            }:
                raise self._registration_error(
                    record.plugin_id,
                    "plugin_id_conflict",
                    f"Plugin cannot be extended in status {current_record.status!r}.",
                )
            for item in staged_values:
                current = self._contributions.get(item.key)
                if current is not None:
                    raise self._registration_error(
                        record.plugin_id,
                        "contribution_conflict",
                        (
                            f"{item.kind} {item.contribution_id!r} is already "
                            f"owned by plugin {current.owner_plugin_id!r}."
                        ),
                        item.key,
                    )
            if current_record is not None and current_record.status == "active":
                current_record.contribution_keys.extend(keys)
                current_record.trust_level = _stronger_trust_level(
                    current_record.trust_level,
                    record.trust_level,
                )
                record = current_record
            else:
                record.status = "active"
                record.contribution_keys = keys
                record.activated_at = datetime.now(timezone.utc).isoformat()
                self._records[record.plugin_id] = record
            for item in staged_values:
                self._contributions[item.key] = item
            self._dispose_callbacks[record.plugin_id] = (
                *self._dispose_callbacks.get(record.plugin_id, ()),
                *dispose_values,
            )
            return deepcopy(record)

    def _record_activation_failure(
        self,
        plugin_id: str,
        exc: Exception,
        metadata: dict[str, Any],
    ) -> None:
        diagnostic = (
            exc.diagnostic
            if isinstance(exc, PluginRegistrationError)
            else PluginDiagnostic(
                plugin_id=plugin_id,
                phase="activate",
                code="activation_failed",
                message=f"Plugin activation failed with {type(exc).__name__}.",
            )
        )
        with self._lock:
            existing = self._records.get(plugin_id)
            if existing is not None and existing.status == "active":
                existing.diagnostics.append(diagnostic)
                return
            self._records[plugin_id] = PluginRecord(
                plugin_id=plugin_id,
                name=str(metadata.get("name") or plugin_id),
                version=metadata.get("version"),
                description=metadata.get("description"),
                source=str(metadata.get("source") or "runtime"),
                api_version=str(metadata.get("api_version") or "1"),
                trust_level=_trust_level(
                    metadata.get("trust_level", "descriptor_only")
                ),
                status="failed",
                diagnostics=[diagnostic],
            )

    @staticmethod
    def _registration_error(
        plugin_id: str,
        code: str,
        message: str,
        key: tuple[str, str] | None = None,
    ) -> PluginRegistrationError:
        return PluginRegistrationError(
            PluginDiagnostic(
                plugin_id=plugin_id,
                phase="register",
                code=code,
                message=message,
                contribution_kind=key[0] if key else None,
                contribution_id=key[1] if key else None,
            )
        )


def _required_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _value_id(value: Any, attribute: str) -> str:
    candidate = getattr(value, attribute, None)
    return _required_id(candidate, attribute)


def _trust_level(value: Any) -> PluginTrustLevel:
    if value not in {
        "builtin",
        "trusted",
        "sandboxed",
        "descriptor_only",
    }:
        raise ValueError(f"Unsupported plugin trust level: {value!r}")
    return value


def _stronger_trust_level(
    current: PluginTrustLevel,
    incoming: PluginTrustLevel,
) -> PluginTrustLevel:
    rank = {
        "descriptor_only": 0,
        "sandboxed": 1,
        "trusted": 2,
        "builtin": 3,
    }
    return current if rank[current] >= rank[incoming] else incoming
