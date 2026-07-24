"""Current-mission memory admission boundary for the Planner.

This module centralises all memory admission policy for the planner context.
Every record that reaches the planner must pass mission, runtime, sensitivity,
and requester scope checks through ``PlannerMemoryContextBuilder.build()``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.memory.embodied_memory import (
    EMBODIED_METADATA_KEY,
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
)
from fireclaw_core.memory.memory_lifecycle import MissionMemoryLifecycleStore
from fireclaw_core.memory.memory_retrieval import (
    MemoryRetrievalScope,
    MemoryRetriever,
)
from fireclaw_core.memory.mission_memory_facade import (
    MEMORY_RESTRICTED_READ_SCOPE,
    MemoryAccessContext,
    MissionMemoryFacade,
)
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission.mission_planning_audit import GuardDecision

MAX_PLANNER_MEMORIES = 100
MAX_PLANNER_CORRECTIONS = 100


@dataclass(frozen=True)
class PlannerMemoryContextRequest:
    command: str
    mission_id: str
    runtime_mode: str | None
    requester_id: str
    scopes: frozenset[str] = frozenset()
    max_memories: int = 5
    max_corrections: int = 3

    def __post_init__(self) -> None:
        for name, value in (
            ("command", self.command),
            ("mission_id", self.mission_id),
            ("requester_id", self.requester_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.runtime_mode is not None and self.runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError("runtime_mode must be a valid memory runtime mode")
        if any(not isinstance(scope, str) or not scope.strip() for scope in self.scopes):
            raise ValueError("scopes must contain non-empty strings")
        if not 0 <= self.max_memories <= MAX_PLANNER_MEMORIES:
            raise ValueError("max_memories must be between 0 and 100")
        if not 0 <= self.max_corrections <= MAX_PLANNER_CORRECTIONS:
            raise ValueError("max_corrections must be between 0 and 100")


@dataclass(frozen=True)
class MemoryContextWarning:
    code: str
    source: str
    count: int = 1
    record_id: str | None = None
    exception_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "source": self.source,
                "count": self.count,
                "record_id": self.record_id,
                "exception_class": self.exception_class,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class PlannerMemoryContextResult:
    memories: tuple[dict[str, Any], ...] = ()
    corrections: tuple[dict[str, Any], ...] = ()
    warnings: tuple[MemoryContextWarning, ...] = ()
    omitted_counts: dict[str, int] = field(default_factory=dict)
    restricted_access_granted: bool = False
    reusable_knowledge_available: bool = False

    def guard_decision(self) -> GuardDecision:
        degraded = bool(self.warnings)
        return GuardDecision(
            layer="memory_context",
            status="allow",
            reason=(
                "memory_context_degraded"
                if degraded
                else "memory_context_validated"
            ),
            message=(
                "Planner memory context was validated with degraded sources."
                if degraded
                else "Planner memory context passed isolation checks."
            ),
            details={
                "accepted_memories": len(self.memories),
                "accepted_corrections": len(self.corrections),
                "omitted_counts": dict(sorted(self.omitted_counts.items())),
                "warning_codes": sorted({warning.code for warning in self.warnings}),
                "restricted_access_granted": self.restricted_access_granted,
                "reusable_knowledge_available": self.reusable_knowledge_available,
            },
        )


class PlannerMemoryContextBuilder:
    """Orchestrates memory admission for the Planner.

    Validates request parameters, builds scope, queries indexed records via
    MemoryRetriever, queries facade events via MissionMemoryFacade, queries
    corrections via MissionMemoryStore, canonicalises all records, handles
    each dependency failure independently, and returns a
    PlannerMemoryContextResult with guard_decision().
    """

    def __init__(
        self,
        *,
        memory_retriever: MemoryRetriever | None = None,
        mission_memory: MissionMemoryStore | None = None,
        facade: MissionMemoryFacade | None = None,
        lifecycle: MissionMemoryLifecycleStore | None = None,
        plugin_runtime: Any | None = None,
    ) -> None:
        self._memory_retriever = memory_retriever
        self._mission_memory = mission_memory
        self._facade = facade
        self._lifecycle = lifecycle
        self._plugin_runtime = plugin_runtime

    @staticmethod
    def _allowed_sensitivities(request: PlannerMemoryContextRequest) -> tuple[str, ...]:
        if (
            "admin" in request.scopes
            or MEMORY_RESTRICTED_READ_SCOPE in request.scopes
        ):
            return ("standard", "restricted")
        return ("standard",)

    @staticmethod
    def _canonical_record(
        record: MissionMemoryRecord,
        request: PlannerMemoryContextRequest,
        allowed_sensitivities: tuple[str, ...],
    ) -> tuple[dict[str, Any] | None, str | None]:
        metadata = record.content.get(EMBODIED_METADATA_KEY)
        if not isinstance(metadata, dict):
            return None, "legacy_metadata_missing"
        runtime_mode = metadata.get("runtime_mode")
        sensitivity = metadata.get("sensitivity")
        if record.mission_id != request.mission_id:
            return None, "mission_scope_mismatch"
        if runtime_mode != request.runtime_mode:
            return None, "runtime_mode_mismatch"
        if sensitivity not in allowed_sensitivities:
            return None, "restricted_scope_denied"
        try:
            event = EmbodiedMemoryEvent.from_mission_record(record)
        except ValueError:
            return None, "legacy_metadata_missing"
        return {
            "memory_scope": "current_mission",
            "record_id": event.event_id,
            "record_type": event.event_type,
            "source_mission_id": event.mission_id,
            "runtime_mode": event.runtime_mode,
            "sensitivity": event.sensitivity,
            "content": redact_dict(dict(event.payload)),
            "advisory_only": True,
            "can_authorize_action": False,
            "requires_current_state_revalidation": True,
        }, None

    def build(self, request: PlannerMemoryContextRequest) -> PlannerMemoryContextResult:
        """Build the planner memory context for the given request.

        Returns an empty valid result when runtime_mode is None.
        """
        if request.runtime_mode is None:
            return PlannerMemoryContextResult(
                restricted_access_granted=(
                    "admin" in request.scopes
                    or MEMORY_RESTRICTED_READ_SCOPE in request.scopes
                ),
                reusable_knowledge_available=self._lifecycle is not None,
            )

        allowed_sensitivities = self._allowed_sensitivities(request)
        restricted_access_granted = (
            "admin" in request.scopes
            or MEMORY_RESTRICTED_READ_SCOPE in request.scopes
        )
        reusable_knowledge_available = self._lifecycle is not None

        memories: list[dict[str, Any]] = []
        corrections: list[dict[str, Any]] = []
        warnings: list[MemoryContextWarning] = []
        omitted_counts: dict[str, int] = {}
        seen_record_ids: set[str] = set()

        def omit(
            code: str,
            source: str,
            *,
            count: int = 1,
            record_id: str | None = None,
            exception: Exception | None = None,
        ) -> None:
            omitted_counts[code] = omitted_counts.get(code, 0) + count
            warnings.append(MemoryContextWarning(
                code=code,
                source=source,
                count=count,
                record_id=record_id,
                exception_class=type(exception).__name__ if exception is not None else None,
            ))

        # Step 1: Build authority map from mission memory
        authority_ids: set[str] = set()
        if self._mission_memory is not None:
            try:
                authority_records = self._mission_memory.list_records(
                    mission_id=request.mission_id,
                )
                authority_ids = {r.record_id for r in authority_records}
            except Exception as exc:
                omit("authority_lookup_failed", "mission_memory", exception=exc)
        else:
            omit("authority_lookup_failed", "mission_memory")

        # Step 2: Query indexed records via MemoryRetriever
        if self._memory_retriever is not None:
            try:
                scope = MemoryRetrievalScope(
                    mission_ids=(request.mission_id,),
                    runtime_modes=(request.runtime_mode,),
                    allowed_sensitivities=allowed_sensitivities,
                )
                retrieved = self._memory_retriever.retrieve(
                    request.command,
                    scope=scope,
                    limit=request.max_memories,
                )
                for item in retrieved:
                    # Reject any retrieved ID missing from the authority map
                    if authority_ids and item.record_id not in authority_ids:
                        omit("authority_record_missing", "memory_retriever",
                             record_id=item.record_id)
                        continue
                    metadata = item.content.get(EMBODIED_METADATA_KEY)
                    if not isinstance(metadata, dict):
                        omit("legacy_metadata_missing", "memory_retriever",
                             record_id=item.record_id)
                        continue
                    runtime_mode = metadata.get("runtime_mode")
                    sensitivity = metadata.get("sensitivity")
                    if item.mission_id != request.mission_id:
                        omit("mission_scope_mismatch", "memory_retriever",
                             record_id=item.record_id)
                        continue
                    if runtime_mode != request.runtime_mode:
                        omit("runtime_mode_mismatch", "memory_retriever",
                             record_id=item.record_id)
                        continue
                    if sensitivity not in allowed_sensitivities:
                        omit("restricted_scope_denied", "memory_retriever",
                             record_id=item.record_id)
                        continue
                    try:
                        event = EmbodiedMemoryEvent.from_mission_record(
                            MissionMemoryRecord(
                                record_id=item.record_id,
                                mission_id=item.mission_id,
                                record_type=item.record_type,
                                content=item.content,
                                created_at=item.created_at,
                            )
                        )
                    except ValueError:
                        omit("legacy_metadata_missing", "memory_retriever",
                             record_id=item.record_id)
                        continue
                    if item.record_id not in seen_record_ids:
                        seen_record_ids.add(item.record_id)
                        memories.append({
                            "memory_scope": "current_mission",
                            "record_id": event.event_id,
                            "record_type": event.event_type,
                            "source_mission_id": event.mission_id,
                            "runtime_mode": event.runtime_mode,
                            "sensitivity": event.sensitivity,
                            "content": redact_dict(dict(event.payload)),
                            "advisory_only": True,
                            "can_authorize_action": False,
                            "requires_current_state_revalidation": True,
                        })
            except Exception as exc:
                omit("memory_retriever_failed", "memory_retriever", exception=exc)

        # Step 3: Query facade events via MissionMemoryFacade
        if self._facade is not None:
            try:
                access = MemoryAccessContext(
                    mission_id=request.mission_id,
                    runtime_mode=request.runtime_mode,
                    requester_id=request.requester_id,
                    scopes=request.scopes,
                )
                context = self._facade.get_current_context(
                    access,
                    limit=request.max_memories,
                )
                # Reconstruct current envelopes from returned events
                facade_events = context.get("events", [])
                for event_payload in facade_events:
                    if not isinstance(event_payload, dict):
                        continue
                    event_id = event_payload.get("event_id")
                    if not event_id or event_id in seen_record_ids:
                        continue
                    # Reject any facade event ID missing from the authority map
                    if authority_ids and event_id not in authority_ids:
                        omit("authority_record_missing", "facade",
                             record_id=event_id)
                        continue
                    sensitivity = event_payload.get("sensitivity", "standard")
                    runtime_mode_val = event_payload.get("runtime_mode")
                    if runtime_mode_val != request.runtime_mode:
                        omit("runtime_mode_mismatch", "facade",
                             record_id=event_id)
                        continue
                    if sensitivity not in allowed_sensitivities:
                        omit("restricted_scope_denied", "facade",
                             record_id=event_id)
                        continue
                    seen_record_ids.add(event_id)
                    memories.append({
                        "memory_scope": "current_mission",
                        "record_id": event_id,
                        "record_type": event_payload.get("event_type", ""),
                        "source_mission_id": request.mission_id,
                        "runtime_mode": request.runtime_mode,
                        "sensitivity": sensitivity,
                        "content": redact_dict(dict(event_payload.get("payload", {}))),
                        "advisory_only": True,
                        "can_authorize_action": False,
                        "requires_current_state_revalidation": True,
                    })
            except Exception as exc:
                omit("facade_query_failed", "facade", exception=exc)

        # Step 4: Query corrections via MissionMemoryStore
        if self._mission_memory is not None:
            try:
                correction_records = self._mission_memory.list_records(
                    mission_id=request.mission_id,
                    record_type="correction",
                )
                for record in correction_records:
                    canonical, reject_code = self._canonical_record(
                        record, request, allowed_sensitivities,
                    )
                    if canonical is None:
                        omit(reject_code or "unknown", "corrections",
                             record_id=record.record_id)
                        continue
                    if record.record_id not in seen_record_ids:
                        seen_record_ids.add(record.record_id)
                        corrections.append(canonical)
                    if len(corrections) >= request.max_corrections:
                        break
            except Exception as exc:
                omit("corrections_query_failed", "corrections", exception=exc)

        return PlannerMemoryContextResult(
            memories=tuple(memories),
            corrections=tuple(corrections),
            warnings=tuple(warnings),
            omitted_counts=omitted_counts,
            restricted_access_granted=restricted_access_granted,
            reusable_knowledge_available=reusable_knowledge_available,
        )
