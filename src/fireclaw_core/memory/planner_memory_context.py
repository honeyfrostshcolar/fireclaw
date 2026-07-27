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
MAX_KNOWLEDGE_DIAGNOSTIC_RECORDS = 500


def _plugin_key(value: dict[str, Any]) -> tuple[str, str] | None:
    """Extract the canonical map key from a plugin-provided value dict.

    Returns ``("current_mission", record_id)`` if the value carries a
    non-empty ``record_id``, ``("reusable_knowledge", knowledge_id)`` if it
    carries a non-empty ``knowledge_id``, or ``None`` for malformed values.
    """
    record_id = value.get("record_id")
    if isinstance(record_id, str) and record_id:
        return ("current_mission", record_id)
    knowledge_id = value.get("knowledge_id")
    if isinstance(knowledge_id, str) and knowledge_id:
        return ("reusable_knowledge", knowledge_id)
    return None


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

    def status(self) -> dict[str, bool]:
        """Return boolean flags indicating which memory sources are configured.

        This is a content-free diagnostic method suitable for runtime wiring
        assertions without exposing source instances.
        """
        return {
            "has_memory_retriever": self._memory_retriever is not None,
            "has_mission_memory": self._mission_memory is not None,
            "has_facade": self._facade is not None,
            "has_lifecycle": self._lifecycle is not None,
            "has_plugin_runtime": self._plugin_runtime is not None,
        }

    @staticmethod
    def _allowed_sensitivities(request: PlannerMemoryContextRequest) -> tuple[str, ...]:
        if (
            "admin" in request.scopes
            or MEMORY_RESTRICTED_READ_SCOPE in request.scopes
        ):
            return ("standard", "restricted")
        return ("standard",)

    @staticmethod
    def _canonical_knowledge(record: Any) -> dict[str, Any]:
        return {
            "memory_scope": "reusable_knowledge",
            "knowledge_id": record.knowledge_id,
            "knowledge_type": record.knowledge_type,
            "title": record.title,
            "tags": list(record.tags),
            "source_mission_id": record.source_mission_id,
            "runtime_mode": record.source_runtime_mode,
            "applicable_runtime_modes": list(record.applicable_runtime_modes),
            "content": redact_dict(dict(record.content)),
            "advisory_only": True,
            "can_authorize_action": False,
            "requires_current_state_revalidation": True,
        }

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
                reusable_knowledge_available=False,
            )

        allowed_sensitivities = self._allowed_sensitivities(request)
        restricted_access_granted = (
            "admin" in request.scopes
            or MEMORY_RESTRICTED_READ_SCOPE in request.scopes
        )
        reusable_knowledge_available = False

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
            exception_class: str | None = None,
        ) -> None:
            omitted_counts[code] = omitted_counts.get(code, 0) + count
            resolved_exception_class = (
                exception_class
                if exception_class is not None
                else (type(exception).__name__ if exception is not None else None)
            )
            warnings.append(MemoryContextWarning(
                code=code,
                source=source,
                count=count,
                record_id=record_id,
                exception_class=resolved_exception_class,
            ))

        # Step 1: Build authority map from mission memory
        authority_available = False
        authority_ids: set[str] = set()
        authority_record_map: dict[str, MissionMemoryRecord] = {}
        if self._mission_memory is not None:
            try:
                authority_records = self._mission_memory.list_records(
                    mission_id=request.mission_id,
                )
                authority_available = True
                authority_ids = {r.record_id for r in authority_records}
                authority_record_map = {r.record_id: r for r in authority_records}
            except Exception as exc:
                omit("current_memory_unavailable", "mission_memory", exception=exc)
        else:
            omit("current_memory_unavailable", "mission_memory")

        # Step 2: Query indexed records via MemoryRetriever
        # Only admit candidates when authority is available; use authority
        # content exclusively (never trust retriever payload).
        if self._memory_retriever is not None and authority_available:
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
                    # The record_id is the only claim; must be in authority map
                    if item.record_id not in authority_record_map:
                        omit("authority_record_missing", "memory_retriever",
                             record_id=item.record_id)
                        continue
                    # Canonicalize from authority, never from retriever payload
                    record = authority_record_map[item.record_id]
                    canonical, reject_code = self._canonical_record(
                        record, request, allowed_sensitivities,
                    )
                    if canonical is None:
                        omit(reject_code or "unknown", "memory_retriever",
                             record_id=item.record_id)
                        continue
                    if record.record_type == "correction":
                        if item.record_id not in seen_record_ids:
                            seen_record_ids.add(item.record_id)
                            corrections.append(canonical)
                    else:
                        if item.record_id not in seen_record_ids:
                            seen_record_ids.add(item.record_id)
                            memories.append(canonical)
            except Exception as exc:
                omit("current_memory_unavailable", "memory_retriever", exception=exc)
        elif self._memory_retriever is not None and not authority_available:
            omit("current_memory_unavailable", "memory_retriever")

        # Step 3: Query facade events via MissionMemoryFacade
        # Only admit candidates when authority is available; use authority
        # content exclusively (never trust facade payload).
        if self._facade is not None and authority_available:
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
                facade_events = context.get("events", [])
                for event_payload in facade_events:
                    if not isinstance(event_payload, dict):
                        continue
                    event_id = event_payload.get("event_id")
                    if not event_id or event_id in seen_record_ids:
                        continue
                    # The event_id is the only claim; must be in authority map
                    if event_id not in authority_record_map:
                        omit("authority_record_missing", "facade",
                             record_id=event_id)
                        continue
                    # Canonicalize from authority, never from facade payload
                    record = authority_record_map[event_id]
                    canonical, reject_code = self._canonical_record(
                        record, request, allowed_sensitivities,
                    )
                    if canonical is None:
                        omit(reject_code or "unknown", "facade",
                             record_id=event_id)
                        continue
                    if record.record_type == "correction":
                        if event_id not in seen_record_ids:
                            seen_record_ids.add(event_id)
                            corrections.append(canonical)
                    else:
                        if event_id not in seen_record_ids:
                            seen_record_ids.add(event_id)
                            memories.append(canonical)
            except Exception as exc:
                omit("current_context_unavailable", "facade", exception=exc)
        elif self._facade is not None and not authority_available:
            omit("current_context_unavailable", "facade")

        # Step 4: Query corrections via MissionMemoryStore
        # Only when authority is available (same store as Step 1).
        if self._mission_memory is not None and authority_available:
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
                omit("corrections_unavailable", "corrections", exception=exc)
        elif self._mission_memory is not None and not authority_available:
            omit("corrections_unavailable", "corrections")

        # Step 5: Query reusable knowledge via MissionMemoryLifecycleStore
        reusable_memories: list[dict[str, Any]] = []
        # Map for plugin claim classification.  Diagnostic-only revoked
        # records are merged here but never enter canonical_map or Planner
        # context.
        knowledge_by_id: dict[str, Any] = {}
        if self._lifecycle is not None:
            try:
                # Primary query: approved candidates only (quota-respecting)
                approved_knowledge = self._lifecycle.list_knowledge(
                    include_revoked=False,
                    limit=MAX_PLANNER_MEMORIES,
                )
                reusable_knowledge_available = True
                # Reverse for newest-first deterministic ordering
                approved_knowledge = list(reversed(approved_knowledge))
                for record in approved_knowledge:
                    knowledge_by_id[record.knowledge_id] = record
                    if request.runtime_mode not in record.applicable_runtime_modes:
                        continue
                    reusable_memories.append(self._canonical_knowledge(record))
            except Exception as exc:
                omit("reusable_knowledge_unavailable", "lifecycle", exception=exc)

            # Diagnostic-only lookup: include revoked for classification.
            # Failure here does not discard a successful approved query.
            # Records outside the MAX_KNOWLEDGE_DIAGNOSTIC_RECORDS window
            # remain plugin_record_unverified (fail-closed).
            if reusable_knowledge_available:
                try:
                    revoked_knowledge = self._lifecycle.list_knowledge(
                        include_revoked=True,
                        limit=MAX_KNOWLEDGE_DIAGNOSTIC_RECORDS,
                    )
                    for record in revoked_knowledge:
                        if record.knowledge_id not in knowledge_by_id:
                            knowledge_by_id[record.knowledge_id] = record
                except Exception:
                    pass  # diagnostic-only; approved candidates remain valid

        # Step 6: Build canonical maps for plugin reauthorization
        # Key: ("current_mission", record_id) or ("reusable_knowledge", knowledge_id)
        canonical_map: dict[tuple[str, str], dict[str, Any]] = {}
        for item in memories:
            key = _plugin_key(item)
            if key is not None:
                canonical_map[key] = item
        for item in reusable_memories:
            key = _plugin_key(item)
            if key is not None:
                canonical_map[key] = item

        # Stage-scoped allowlists: each plugin stage (filter, rerank) can
        # only authorize candidates that were in that stage's input.
        # A candidate removed by filter cannot be reintroduced by rerank.
        def _reauthorize_plugin_items(
            plugin_items: list[dict[str, Any]],
            allowed_keys: set[tuple[str, str]],
        ) -> list[dict[str, Any]]:
            """Replace every plugin object with its canonical equivalent.

            Only items whose key is in ``allowed_keys`` may be authorized.
            Classifies rejections deterministically:
            - ``knowledge_not_approved``: known knowledge ID that is revoked
              or runtime-mismatched
            - ``plugin_record_unverified``: unknown ID or not in the stage
              allowlist
            """
            reauthorized: list[dict[str, Any]] = []
            for item in plugin_items:
                if not isinstance(item, dict):
                    omit("plugin_record_unverified", "plugin")
                    continue
                key = _plugin_key(item)
                if key is None:
                    omit("plugin_record_unverified", "plugin")
                    continue
                if key not in allowed_keys:
                    # Reject out-of-stage claims fail-closed, while preserving
                    # the more precise lifecycle diagnosis for known
                    # non-approved reusable knowledge.
                    known_knowledge = (
                        knowledge_by_id.get(key[1])
                        if key[0] == "reusable_knowledge"
                        else None
                    )
                    if known_knowledge is not None and (
                        getattr(known_knowledge, "status", None) != "approved"
                        or request.runtime_mode
                        not in getattr(
                            known_knowledge,
                            "applicable_runtime_modes",
                            (),
                        )
                    ):
                        omit(
                            "knowledge_not_approved",
                            "plugin",
                            record_id=key[1],
                        )
                    else:
                        omit("plugin_record_unverified", "plugin")
                    continue
                canonical = canonical_map.get(key)
                if canonical is None:
                    # Key was in the stage input but missing from canonical
                    # map (should not happen); reject unverified
                    omit("plugin_record_unverified", "plugin")
                    continue
                reauthorized.append(canonical)
            return reauthorized

        # Step 7: Run plugin filter and rerank hooks
        # Hooks receive a combined list: current-mission memories + approved
        # reusable knowledge.  After each effect, items are reauthorized and
        # partitioned back so the final quota can prioritize current-mission.
        if self._plugin_runtime is not None:
            # Build combined authorized candidate list for filter
            filter_candidates = list(memories) + list(reusable_memories)
            filter_allowed_keys: set[tuple[str, str]] = set()
            for item in filter_candidates:
                key = _plugin_key(item)
                if key is not None:
                    filter_allowed_keys.add(key)

            # Filter
            try:
                filter_report = self._plugin_runtime.run_memory_hooks_with_diagnostics(
                    "filter",
                    {"command": request.command, "memories": filter_candidates},
                )
                for failure in filter_report.failures:
                    omit(
                        "plugin_filter_failed",
                        f"plugin.{failure.plugin_id}",
                        exception_class=failure.exception_class,
                    )
                if filter_report.effects:
                    for effect_entry in filter_report.effects:
                        effect = effect_entry.get("effect", {})
                        plugin_filtered = effect.get("memories", [])
                        if isinstance(plugin_filtered, list):
                            reauthorized = _reauthorize_plugin_items(
                                plugin_filtered, filter_allowed_keys,
                            )
                            memories[:] = [
                                item for item in reauthorized
                                if item.get("memory_scope") == "current_mission"
                            ]
                            reusable_memories[:] = [
                                item for item in reauthorized
                                if item.get("memory_scope") == "reusable_knowledge"
                            ]
            except Exception as exc:
                omit("plugin_filter_failed", "plugin_runtime", exception=exc)

            # Build rerank candidates from post-filter surviving items only.
            # A candidate removed by filter is not in rerank's allowlist.
            rerank_candidates = list(memories) + list(reusable_memories)
            rerank_allowed_keys: set[tuple[str, str]] = set()
            for item in rerank_candidates:
                key = _plugin_key(item)
                if key is not None:
                    rerank_allowed_keys.add(key)

            # Rerank
            try:
                rerank_report = self._plugin_runtime.run_memory_hooks_with_diagnostics(
                    "rerank",
                    {"command": request.command, "memories": rerank_candidates},
                )
                for failure in rerank_report.failures:
                    omit(
                        "plugin_rerank_failed",
                        f"plugin.{failure.plugin_id}",
                        exception_class=failure.exception_class,
                    )
                if rerank_report.effects:
                    for effect_entry in rerank_report.effects:
                        effect = effect_entry.get("effect", {})
                        plugin_reranked = effect.get("memories", [])
                        if isinstance(plugin_reranked, list):
                            reauthorized = _reauthorize_plugin_items(
                                plugin_reranked, rerank_allowed_keys,
                            )
                            memories[:] = [
                                item for item in reauthorized
                                if item.get("memory_scope") == "current_mission"
                            ]
                            reusable_memories[:] = [
                                item for item in reauthorized
                                if item.get("memory_scope") == "reusable_knowledge"
                            ]
            except Exception as exc:
                omit("plugin_rerank_failed", "plugin_runtime", exception=exc)

            # Provider enrichment (also uses combined list)
            combined_for_hooks = list(memories) + list(reusable_memories)
            try:
                enrich_report = self._plugin_runtime.run_provider_hooks_with_diagnostics(
                    "enrich_context",
                    {
                        "command": request.command,
                        "memories": combined_for_hooks,
                    },
                )
                for failure in enrich_report.failures:
                    omit(
                        "plugin_enrichment_failed",
                        f"plugin.{failure.plugin_id}",
                        exception_class=failure.exception_class,
                    )
                if enrich_report.effects:
                    seen_enrichment_keys: set[tuple[str, str]] = set()
                    for effect_entry in enrich_report.effects:
                        effect = effect_entry.get("effect", {})
                        enrichment_items = effect.get("retrieved_memories", effect.get("memories", []))
                        if not isinstance(enrichment_items, list):
                            continue
                        for item in enrichment_items:
                            if not isinstance(item, dict):
                                omit("plugin_record_unverified", "plugin")
                                continue
                            key = _plugin_key(item)
                            if key is None:
                                omit("plugin_record_unverified", "plugin")
                                continue
                            canonical = canonical_map.get(key)
                            if canonical is None:
                                # For current_mission records, reload from
                                # the full authority map and revalidate
                                if key[0] == "current_mission":
                                    canonical = self._resolve_enrichment_record(
                                        key[1], request, allowed_sensitivities,
                                        authority_ids, seen_record_ids,
                                        authority_record_map,
                                    )
                                elif key[0] == "reusable_knowledge":
                                    canonical = self._resolve_enrichment_knowledge(
                                        key[1], request, knowledge_by_id,
                                    )
                            if canonical is None:
                                # Classify the rejection
                                if key[0] == "current_mission" and key[1] in authority_record_map:
                                    omit("plugin_scope_mismatch", "plugin", record_id=key[1])
                                elif key[0] == "reusable_knowledge" and key[1] in knowledge_by_id:
                                    omit("knowledge_not_approved", "plugin", record_id=key[1])
                                else:
                                    omit("plugin_record_unverified", "plugin")
                                continue
                            # Deduplicate against memories and previously
                            # enriched items
                            canonical_key = _plugin_key(canonical)
                            if canonical_key is None:
                                continue
                            if canonical_key in seen_enrichment_keys:
                                continue
                            all_existing_keys = {
                                _plugin_key(m) for m in (memories + reusable_memories)
                                if _plugin_key(m) is not None
                            }
                            if canonical_key in all_existing_keys:
                                continue
                            seen_enrichment_keys.add(canonical_key)
                            if canonical.get("record_type") == "correction":
                                corrections.append(canonical)
                            else:
                                memories.append(canonical)
            except Exception as exc:
                omit("plugin_enrichment_failed", "plugin_runtime", exception=exc)

        # Step 8: Merge reusable knowledge and apply deterministic final quotas.
        # Reusable items from the lifecycle query (Step 5) are merged here;
        # plugin enrichment may have already added some.  Deduplicate by key.
        all_items = list(memories) + list(reusable_memories)
        deduplicated: list[dict[str, Any]] = []
        deduplicated_keys: set[tuple[str, str]] = set()
        for item in all_items:
            key = _plugin_key(item)
            if key is not None and key in deduplicated_keys:
                continue
            if key is not None:
                deduplicated_keys.add(key)
            deduplicated.append(item)
        memories = deduplicated

        current = [
            item for item in memories
            if item["memory_scope"] == "current_mission"
        ]
        reusable = [
            item for item in memories
            if item["memory_scope"] == "reusable_knowledge"
        ]
        final_memories = current[:request.max_memories]
        final_memories.extend(
            reusable[:max(0, request.max_memories - len(final_memories))]
        )
        final_corrections = corrections[:request.max_corrections]

        return PlannerMemoryContextResult(
            memories=tuple(final_memories),
            corrections=tuple(final_corrections),
            warnings=tuple(warnings),
            omitted_counts=omitted_counts,
            restricted_access_granted=restricted_access_granted,
            reusable_knowledge_available=reusable_knowledge_available,
        )

    def _resolve_enrichment_record(
        self,
        record_id: str,
        request: PlannerMemoryContextRequest,
        allowed_sensitivities: tuple[str, ...],
        authority_ids: set[str],
        seen_record_ids: set[str],
        authority_record_map: dict[str, MissionMemoryRecord] | None = None,
    ) -> dict[str, Any] | None:
        """Resolve an enrichment record_id against the full authority map.

        Returns a canonical dict if the record passes all checks, or None.
        """
        if self._mission_memory is None:
            return None
        if authority_ids and record_id not in authority_ids:
            return None
        if record_id in seen_record_ids:
            # Already in the memories list; return None to avoid duplicate
            # (the deduplication in the caller handles this)
            return None
        try:
            if authority_record_map is not None:
                # Use cached record map to avoid redundant list_records()
                record = authority_record_map.get(record_id)
                if record is None:
                    return None
                canonical, _reject_code = self._canonical_record(
                    record, request, allowed_sensitivities,
                )
                if canonical is not None:
                    seen_record_ids.add(record_id)
                    return canonical
            else:
                records = self._mission_memory.list_records(
                    mission_id=request.mission_id,
                )
                for record in records:
                    if record.record_id != record_id:
                        continue
                    canonical, _reject_code = self._canonical_record(
                        record, request, allowed_sensitivities,
                    )
                    if canonical is not None:
                        seen_record_ids.add(record_id)
                        return canonical
        except Exception:
            return None
        return None

    def _resolve_enrichment_knowledge(
        self,
        knowledge_id: str,
        request: PlannerMemoryContextRequest,
        knowledge_by_id: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve an enrichment knowledge_id against the cached knowledge dict.

        Returns a canonical dict if the knowledge is approved and applicable,
        or None.  Uses ``knowledge_by_id`` (populated in ``build()`` Step 5)
        to avoid a redundant ``list_knowledge()`` call.
        """
        record = knowledge_by_id.get(knowledge_id)
        if record is None:
            return None
        if record.status != "approved":
            return None
        if request.runtime_mode not in record.applicable_runtime_modes:
            return None
        return self._canonical_knowledge(record)
