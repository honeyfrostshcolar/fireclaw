from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Any, Iterable

from fireclaw_core.mission.mission_planner import (
    MissionPlannerContext,
    MissionPlanningResult,
)
from fireclaw_core.mission.mission_state import MissionStateSnapshot


@dataclass(frozen=True)
class MissionPlanningContextBudget:
    """Host-owned limits for dynamic mission-planning context."""

    max_dynamic_chars: int = 32_000
    max_item_chars: int = 8_000
    max_operator_corrections: int = 8
    max_memories: int = 8
    max_external_knowledge: int = 8
    max_agent_tool_observations: int = 6

    def __post_init__(self) -> None:
        for name, value in (
            ("max_dynamic_chars", self.max_dynamic_chars),
            ("max_item_chars", self.max_item_chars),
            ("max_operator_corrections", self.max_operator_corrections),
            ("max_memories", self.max_memories),
            ("max_external_knowledge", self.max_external_knowledge),
            (
                "max_agent_tool_observations",
                self.max_agent_tool_observations,
            ),
        ):
            if value < 1:
                raise ValueError(
                    f"Mission planning context {name} must be positive."
                )


@dataclass(frozen=True)
class MissionContextSectionManifest:
    name: str
    source: str
    trust: str
    critical: bool
    included_count: int
    omitted_count: int
    included_chars: int
    omission_reasons: tuple[tuple[str, int], ...] = ()
    included_refs: tuple[str, ...] = ()
    omitted_refs: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "trust": self.trust,
            "critical": self.critical,
            "included_count": self.included_count,
            "omitted_count": self.omitted_count,
            "included_chars": self.included_chars,
            "omission_reasons": {
                reason: count for reason, count in self.omission_reasons
            },
            "included_refs": list(self.included_refs),
            "omitted_refs": [
                {"ref": reference, "reason": reason}
                for reference, reason in self.omitted_refs
            ],
        }


@dataclass(frozen=True)
class MissionPlanningContextManifest:
    context_id: str
    max_dynamic_chars: int
    used_dynamic_chars: int
    critical_chars: int
    advisory_chars: int
    safety_critical_context_preserved: bool
    sections: tuple[MissionContextSectionManifest, ...]
    model_context: dict[str, Any] | None = None

    @property
    def omitted_count(self) -> int:
        return sum(section.omitted_count for section in self.sections)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "context_id": self.context_id,
            "max_dynamic_chars": self.max_dynamic_chars,
            "used_dynamic_chars": self.used_dynamic_chars,
            "critical_chars": self.critical_chars,
            "advisory_chars": self.advisory_chars,
            "safety_critical_context_preserved": (
                self.safety_critical_context_preserved
            ),
            "omitted_count": self.omitted_count,
            "sections": [section.to_dict() for section in self.sections],
        }
        if self.model_context is not None:
            result["model_context"] = dict(self.model_context)
        return result


@dataclass(frozen=True)
class MissionPlanningContextEnvelope:
    """Exact dynamic context projected for one model decision."""

    context_id: str
    authoritative: dict[str, Any]
    continuity: dict[str, Any]
    advisory: dict[str, tuple[dict[str, Any], ...]]
    manifest: MissionPlanningContextManifest

    def to_prompt_dict(self) -> dict[str, Any]:
        context_policy = {
            "safety_critical_context_preserved": (
                self.manifest.safety_critical_context_preserved
            ),
            "omitted_advisory_items": self.manifest.omitted_count,
            "used_dynamic_chars": self.manifest.used_dynamic_chars,
            "max_dynamic_chars": self.manifest.max_dynamic_chars,
        }
        if self.manifest.model_context is not None:
            model_context = self.manifest.model_context
            for key in (
                "scope",
                "model_id",
                "context_window_tokens",
                "output_reserve_tokens",
                "safety_margin_tokens",
                "max_input_tokens",
                "used_input_tokens",
                "token_counter",
                "exact_model_tokenizer",
                "semantic_compaction_count",
            ):
                if key in model_context:
                    context_policy[key] = model_context[key]
            omitted = model_context.get("omitted_refs")
            if isinstance(omitted, list):
                context_policy["omitted_advisory_items"] += len(omitted)
        return {
            "context_id": self.context_id,
            "authoritative": dict(self.authoritative),
            "continuity": dict(self.continuity),
            "advisory": {
                name: [dict(item) for item in items]
                for name, items in self.advisory.items()
            },
            "context_policy": context_policy,
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.to_prompt_dict()
        result["manifest"] = self.manifest.to_dict()
        return result


@dataclass(frozen=True)
class MissionPlanningContextAssembly:
    envelope: MissionPlanningContextEnvelope
    planner_context: MissionPlannerContext


class MissionPlanningContextAssemblyError(ValueError):
    pass


class MissionPlanningContextAssembler:
    """Project trusted state and bounded advisory context before model calls."""

    def __init__(
        self,
        budget: MissionPlanningContextBudget | None = None,
    ) -> None:
        self.budget = budget or MissionPlanningContextBudget()

    def assemble(
        self,
        *,
        mission_id: str,
        command: str,
        state_snapshot: MissionStateSnapshot,
        planner_context: MissionPlannerContext,
        iteration: int,
        observations: Iterable[Any] = (),
        validation_errors: Iterable[str] = (),
        last_planning_result: MissionPlanningResult | None = None,
        plan_revision: int = 1,
        supersedes_plan_id: str | None = None,
        invalidation_evidence_ids: tuple[str, ...] = (),
    ) -> MissionPlanningContextAssembly:
        serialized_items = [
            (
                self._mapping(item.to_dict()),
                bool(getattr(item, "authoritative", True)),
            )
            for item in observations
        ]
        serialized_observations = [
            value for value, authoritative in serialized_items
            if authoritative
        ]
        agent_tool_observations = [
            value for value, authoritative in serialized_items
            if not authoritative
        ]
        exposed_belief_ids = self._observed_belief_ids(
            serialized_observations
        )
        authoritative = {
            "mission_id": mission_id,
            "operator_command": command,
            "iteration": iteration,
            "plan_revision": plan_revision,
            "snapshot_contract": self._snapshot_contract(state_snapshot),
            "available_robots": [
                {
                    "robot_id": robot.robot_id,
                    "capabilities": list(robot.capabilities),
                    "enabled": robot.enabled,
                    "zone": robot.zone,
                }
                for robot in planner_context.available_robots
            ],
            "observations": serialized_observations,
            "tool_exposed_belief_ids": list(exposed_belief_ids),
            "validation_errors": [
                str(error) for error in validation_errors
            ],
            "invalidation_evidence_ids": list(
                invalidation_evidence_ids
            ),
        }
        continuity = {
            "supersedes_plan_id": supersedes_plan_id,
            "last_plan_proposal": self._last_plan(last_planning_result),
        }
        advisory: dict[str, list[dict[str, Any]]] = {
            "operator_corrections": [],
            "retrieved_memories": [],
            "external_knowledge": [],
            "agent_tool_observations": [],
        }
        critical_payload = {
            "authoritative": authoritative,
            "continuity": continuity,
        }
        try:
            critical_chars = _json_chars(critical_payload)
            base_chars = _json_chars({
                **critical_payload,
                "advisory": advisory,
            })
        except (TypeError, ValueError) as exc:
            raise MissionPlanningContextAssemblyError(
                "Safety-critical mission planning context is not JSON "
                "serializable."
            ) from exc
        if base_chars > self.budget.max_dynamic_chars:
            raise MissionPlanningContextAssemblyError(
                "Safety-critical mission planning context exceeds "
                f"{self.budget.max_dynamic_chars} characters; it will not be "
                "silently truncated."
            )

        section_manifests: list[MissionContextSectionManifest] = [
            self._critical_manifest(
                "mission_state_contract",
                "mission_runtime",
                authoritative,
                included_count=1,
            ),
            self._critical_manifest(
                "observations",
                "frozen_mission_snapshot",
                authoritative["observations"],
                included_count=len(authoritative["observations"]),
            ),
            self._critical_manifest(
                "validation_feedback",
                "deterministic_validators",
                authoritative["validation_errors"],
                included_count=len(authoritative["validation_errors"]),
            ),
            self._critical_manifest(
                "plan_continuity",
                "mission_runtime",
                continuity,
                included_count=(
                    1 if continuity["last_plan_proposal"] is not None else 0
                ),
            ),
        ]

        section_specs = (
            (
                "agent_tool_observations",
                "agent_tool_runtime",
                "tool_advisory",
                agent_tool_observations,
                self.budget.max_agent_tool_observations,
            ),
            (
                "operator_corrections",
                "operator_memory",
                "operator_advisory",
                planner_context.operator_corrections,
                self.budget.max_operator_corrections,
            ),
            (
                "retrieved_memories",
                "mission_memory",
                "advisory",
                planner_context.retrieved_memories,
                self.budget.max_memories,
            ),
            (
                "external_knowledge",
                "external_knowledge_rag",
                "untrusted_advisory",
                planner_context.external_knowledge,
                self.budget.max_external_knowledge,
            ),
        )
        for name, source, trust, items, max_items in section_specs:
            included, manifest = self._select_advisory_items(
                name=name,
                source=source,
                trust=trust,
                items=items,
                max_items=max_items,
                authoritative=authoritative,
                continuity=continuity,
                advisory=advisory,
            )
            advisory[name] = included
            section_manifests.append(manifest)

        prompt_content = {
            "authoritative": authoritative,
            "continuity": continuity,
            "advisory": advisory,
        }
        used_chars = _json_chars(prompt_content)
        digest = sha256(
            _json_text(prompt_content).encode("utf-8")
        ).hexdigest()[:12]
        context_id = (
            f"{mission_id}:context:{state_snapshot.version}:"
            f"{plan_revision}:{iteration}:{digest}"
        )
        advisory_chars = max(0, used_chars - critical_chars)
        manifest = MissionPlanningContextManifest(
            context_id=context_id,
            max_dynamic_chars=self.budget.max_dynamic_chars,
            used_dynamic_chars=used_chars,
            critical_chars=critical_chars,
            advisory_chars=advisory_chars,
            safety_critical_context_preserved=True,
            sections=tuple(section_manifests),
        )
        frozen_advisory = {
            name: tuple(items) for name, items in advisory.items()
        }
        envelope = MissionPlanningContextEnvelope(
            context_id=context_id,
            authoritative=authoritative,
            continuity=continuity,
            advisory=frozen_advisory,
            manifest=manifest,
        )
        projected_context = replace(
            planner_context,
            retrieved_memories=list(
                frozen_advisory["retrieved_memories"]
            ),
            operator_corrections=list(
                frozen_advisory["operator_corrections"]
            ),
            external_knowledge=list(
                frozen_advisory["external_knowledge"]
            ),
            tool_exposed_belief_ids=exposed_belief_ids,
        )
        return MissionPlanningContextAssembly(
            envelope=envelope,
            planner_context=projected_context,
        )

    def _select_advisory_items(
        self,
        *,
        name: str,
        source: str,
        trust: str,
        items: Iterable[Any],
        max_items: int,
        authoritative: dict[str, Any],
        continuity: dict[str, Any],
        advisory: dict[str, list[dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], MissionContextSectionManifest]:
        included: list[dict[str, Any]] = []
        omission_reasons: dict[str, int] = {}
        included_refs: list[str] = []
        omitted_refs: list[tuple[str, str]] = []
        seen: set[str] = set()

        def omit(reference: str, reason: str) -> None:
            omission_reasons[reason] = (
                omission_reasons.get(reason, 0) + 1
            )
            omitted_refs.append((reference, reason))

        for index, item in enumerate(items):
            reference = _item_reference(item, index=index)
            if not isinstance(item, dict):
                omit(reference, "invalid_item")
                continue
            try:
                canonical = _json_text(item)
            except (TypeError, ValueError):
                omit(reference, "invalid_item")
                continue
            if canonical in seen:
                omit(reference, "duplicate_item")
                continue
            seen.add(canonical)
            if len(included) >= max_items:
                omit(reference, "item_limit")
                continue
            if len(canonical) > self.budget.max_item_chars:
                omit(reference, "item_too_large")
                continue
            candidate = [*included, dict(item)]
            candidate_advisory = {
                **advisory,
                name: candidate,
            }
            candidate_chars = _json_chars({
                "authoritative": authoritative,
                "continuity": continuity,
                "advisory": candidate_advisory,
            })
            if candidate_chars > self.budget.max_dynamic_chars:
                omit(reference, "dynamic_budget")
                continue
            included = candidate
            included_refs.append(reference)

        return included, MissionContextSectionManifest(
            name=name,
            source=source,
            trust=trust,
            critical=False,
            included_count=len(included),
            omitted_count=sum(omission_reasons.values()),
            included_chars=_json_chars(included),
            omission_reasons=tuple(sorted(omission_reasons.items())),
            included_refs=tuple(included_refs),
            omitted_refs=tuple(omitted_refs),
        )

    @staticmethod
    def _snapshot_contract(
        snapshot: MissionStateSnapshot,
    ) -> dict[str, Any]:
        return {
            "snapshot_id": snapshot.snapshot_id,
            "version": snapshot.version,
            "captured_at": snapshot.captured_at,
            "previous_snapshot_id": snapshot.previous_snapshot_id,
            "belief_projection_version": (
                snapshot.belief_projection_version
            ),
            "environment_belief_summary": {
                status: sum(
                    belief.status == status
                    for belief in snapshot.environment_beliefs
                )
                for status in (
                    "confirmed",
                    "uncertain",
                    "conflicted",
                    "stale",
                )
            },
            "robot_count": len(snapshot.robots),
            "task_count": len(snapshot.tasks),
            "environment_fact_count": len(
                snapshot.environment_facts
            ),
            "environment_belief_count": len(
                snapshot.environment_beliefs
            ),
            "resource_reservation_count": len(
                snapshot.resource_reservations
            ),
        }

    @staticmethod
    def _last_plan(
        result: MissionPlanningResult | None,
    ) -> dict[str, Any] | None:
        if result is None:
            return None
        return {
            "status": result.status,
            "message": result.message,
            "intent": result.intent,
            "plan": (
                result.plan.to_dict()
                if result.plan is not None
                else None
            ),
            "graph_proposal": (
                result.graph_proposal.to_dict()
                if result.graph_proposal is not None
                else None
            ),
        }

    @staticmethod
    def _observed_belief_ids(
        observations: Iterable[dict[str, Any]],
    ) -> tuple[str, ...]:
        belief_ids: set[str] = set()
        for observation in observations:
            if observation.get("kind") != "environment_beliefs":
                continue
            data = observation.get("data")
            if not isinstance(data, dict):
                continue
            beliefs = data.get("environment_beliefs")
            if not isinstance(beliefs, list):
                continue
            for belief in beliefs:
                if not isinstance(belief, dict):
                    continue
                belief_id = belief.get("belief_id")
                if isinstance(belief_id, str) and belief_id.strip():
                    belief_ids.add(belief_id)
        return tuple(sorted(belief_ids))

    @staticmethod
    def _critical_manifest(
        name: str,
        source: str,
        value: Any,
        *,
        included_count: int,
    ) -> MissionContextSectionManifest:
        return MissionContextSectionManifest(
            name=name,
            source=source,
            trust="authoritative",
            critical=True,
            included_count=included_count,
            omitted_count=0,
            included_chars=_json_chars(value),
        )

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise MissionPlanningContextAssemblyError(
                "Mission planning observation must serialize to an object."
            )
        return dict(value)


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_chars(value: Any) -> int:
    return len(_json_text(value))


def _item_reference(value: Any, *, index: int) -> str:
    if isinstance(value, dict):
        for key in (
            "record_id",
            "knowledge_id",
            "event_id",
            "fact_id",
            "belief_id",
            "id",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        try:
            digest = sha256(
                _json_text(value).encode("utf-8")
            ).hexdigest()[:12]
            return f"item:{digest}"
        except (TypeError, ValueError):
            pass
    return f"item:{index}"
