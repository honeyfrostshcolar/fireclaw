"""Safety-aware structured analysis used by embodied-memory consolidation."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent, SpatialMemoryContext


class GistSummarizer(Protocol):
    method_id: str

    def summarize(self, context: dict[str, Any]) -> str:
        """Return generated summary text from bounded structured context."""
        ...


@dataclass(frozen=True)
class DeterministicGistSummarizer:
    method_id: str = "fireclaw.memory.deterministic_gist_summary:v2"

    def summarize(self, context: dict[str, Any]) -> str:
        counts = context.get("event_type_counts", {})
        count_text = ", ".join(
            f"{name}={count}" for name, count in sorted(counts.items())
        )
        safety = context.get("safety_decisions", [])
        decisions = ", ".join(
            str(record.get("decision"))
            for record in safety
            if record.get("decision") is not None
        ) or "none"
        layers = context.get("evidence_layers", [])
        layer_text = ", ".join(
            f"{layer.get('layer_name')}={layer.get('event_count')}"
            for layer in layers
        ) or "none"
        assessment = context.get("cross_robot_assessment", {})
        return (
            f"Recorded events: {count_text}. Evidence layers: {layer_text}. "
            f"Safety decisions: {decisions}. Cross-robot agreements: "
            f"{assessment.get('agreement_count', 0)}; contradictions: "
            f"{assessment.get('contradiction_count', 0)}. Operator corrections retained: "
            f"{len(context.get('operator_correction_event_ids', []))}."
        )


@dataclass(frozen=True)
class _Claim:
    key: str
    value: Any
    value_key: str
    event_id: str
    robot_id: str
    observed_at: str
    scope_key: str | None
    pose: SpatialMemoryContext | None
    mission_shared_frame: bool


def build_evidence_layers(events: list[EmbodiedMemoryEvent]) -> list[dict[str, Any]]:
    grouped: dict[str, list[EmbodiedMemoryEvent]] = {}
    for event in events:
        if event.event_type != "observation":
            continue
        grouped.setdefault(_observation_layer(event), []).append(event)
    result: list[dict[str, Any]] = []
    for layer_name in sorted(grouped):
        layer_events = grouped[layer_name]
        result.append({
            "layer_name": layer_name,
            "event_count": len(layer_events),
            "event_ids": [event.event_id for event in layer_events],
            "robot_ids": sorted({event.robot_id for event in layer_events if event.robot_id}),
            "source_types": sorted({event.source_type for event in layer_events}),
            "sensor_ids": sorted({
                event.provenance.sensor_id
                for event in layer_events
                if event.provenance is not None and event.provenance.sensor_id
            }),
        })
    return result


def build_spatial_geometries(
    events: list[EmbodiedMemoryEvent],
    *,
    cluster_margin_m: float,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str | None], list[EmbodiedMemoryEvent]] = {}
    for event in events:
        if event.pose is None:
            continue
        groups.setdefault((event.pose.frame_id, event.pose.floor), []).append(event)

    geometries: list[dict[str, Any]] = []
    for (frame_id, floor), positioned in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        for cluster in _uncertainty_clusters(positioned, cluster_margin_m):
            poses = [event.pose for event in cluster if event.pose is not None]
            min_x = min(pose.x - pose.uncertainty_radius_m for pose in poses)
            max_x = max(pose.x + pose.uncertainty_radius_m for pose in poses)
            min_y = min(pose.y - pose.uncertainty_radius_m for pose in poses)
            max_y = max(pose.y + pose.uncertainty_radius_m for pose in poses)
            center_x = (min_x + max_x) / 2.0
            center_y = (min_y + max_y) / 2.0
            radius_m = max(
                math.hypot(pose.x - center_x, pose.y - center_y)
                + pose.uncertainty_radius_m
                for pose in poses
            )
            z_poses = [pose for pose in poses if pose.z is not None]
            geometry: dict[str, Any] = {
                "geometry_kind": "uncertainty_envelope",
                "frame_id": frame_id,
                "floor": floor,
                "center_x": center_x,
                "center_y": center_y,
                "radius_m": radius_m,
                "bounds": {
                    "min_x": min_x,
                    "max_x": max_x,
                    "min_y": min_y,
                    "max_y": max_y,
                },
                "position_count": len(poses),
                "source_event_ids": [event.event_id for event in cluster],
                "robot_ids": sorted({event.robot_id for event in cluster if event.robot_id}),
                "frame_scope": (
                    "mission"
                    if all(
                        event.payload.get("spatial_frame_scope") == "mission"
                        for event in cluster
                    )
                    else "source_local_or_unspecified"
                ),
                "advisory_only": True,
            }
            if len(z_poses) == len(poses):
                min_z = min(
                    float(pose.z) - pose.uncertainty_radius_m
                    for pose in z_poses
                )
                max_z = max(
                    float(pose.z) + pose.uncertainty_radius_m
                    for pose in z_poses
                )
                geometry["center_z"] = (min_z + max_z) / 2.0
                geometry["bounds"]["min_z"] = min_z
                geometry["bounds"]["max_z"] = max_z
                geometry["vertical_bounds_include_uncertainty"] = True
            geometries.append(geometry)
    return geometries


def gist_pose_from_geometries(
    geometries: list[dict[str, Any]],
) -> SpatialMemoryContext | None:
    if len(geometries) != 1:
        return None
    geometry = geometries[0]
    return SpatialMemoryContext(
        frame_id=str(geometry["frame_id"]),
        x=float(geometry["center_x"]),
        y=float(geometry["center_y"]),
        z=(float(geometry["center_z"]) if geometry.get("center_z") is not None else None),
        floor=(str(geometry["floor"]) if geometry.get("floor") is not None else None),
        uncertainty_radius_m=float(geometry["radius_m"]),
    )


def assess_cross_robot_claims(
    source_events: list[EmbodiedMemoryEvent],
    mission_events: list[EmbodiedMemoryEvent],
    *,
    window_seconds: float,
    spatial_margin_m: float,
    max_items: int,
) -> dict[str, Any]:
    source_claims = [claim for event in source_events for claim in _claims(event)]
    source_ids = {event.event_id for event in source_events}
    source_robots = {claim.robot_id for claim in source_claims}
    if not source_claims or not source_robots:
        return _empty_assessment("no_comparable_source_claims")

    start = min(_parse_time(event.observed_at) for event in source_events)
    end = max(_parse_time(event.observed_at) for event in source_events)
    lower = start - timedelta(seconds=window_seconds)
    upper = end + timedelta(seconds=window_seconds)
    candidate_claims = [
        claim
        for event in mission_events
        if event.event_id not in source_ids
        and event.robot_id not in source_robots
        and lower <= _parse_time(event.observed_at) <= upper
        for claim in _claims(event)
    ]

    comparable: list[_Claim] = list(source_claims)
    for candidate in candidate_claims:
        if any(
            source.key == candidate.key
            and _claims_are_spatially_comparable(source, candidate, spatial_margin_m)
            for source in source_claims
        ):
            comparable.append(candidate)

    grouped: dict[str, list[_Claim]] = {}
    for claim in comparable:
        grouped.setdefault(claim.key, []).append(claim)

    agreements: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    compared_event_ids: set[str] = set()
    compared_robot_ids: set[str] = set()
    for key in sorted(grouped):
        claims = grouped[key]
        values: dict[str, list[_Claim]] = {}
        for claim in claims:
            values.setdefault(claim.value_key, []).append(claim)
        represented_robots = {claim.robot_id for claim in claims}
        if len(represented_robots) < 2:
            continue
        compared_event_ids.update(claim.event_id for claim in claims)
        compared_robot_ids.update(represented_robots)
        for value_claims in values.values():
            robots = sorted({claim.robot_id for claim in value_claims})
            if len(robots) >= 2:
                agreements.append({
                    "claim_key": key,
                    "value": value_claims[0].value,
                    "robot_ids": robots,
                    "event_ids": sorted({claim.event_id for claim in value_claims}),
                })
        if len(values) > 1:
            contradictions.append({
                "claim_key": key,
                "values": [
                    {
                        "value": value_claims[0].value,
                        "robot_ids": sorted({claim.robot_id for claim in value_claims}),
                        "event_ids": sorted({claim.event_id for claim in value_claims}),
                    }
                    for _, value_claims in sorted(values.items())
                ],
            })

    agreements = agreements[:max_items]
    contradictions = contradictions[:max_items]
    if contradictions:
        status = "contradictions_present"
    elif agreements:
        status = "agreements_only"
    elif compared_robot_ids:
        status = "comparable_without_match"
    else:
        status = "no_cross_robot_comparison"
    return {
        "status": status,
        "agreement_count": len(agreements),
        "contradiction_count": len(contradictions),
        "agreements": agreements,
        "contradictions": contradictions,
        "compared_robot_ids": sorted(compared_robot_ids),
        "compared_event_ids": sorted(compared_event_ids),
        "comparison_window_seconds": window_seconds,
        "spatial_margin_m": spatial_margin_m,
        "advisory_only": True,
        "requires_current_state_revalidation": True,
    }


def _uncertainty_clusters(
    events: list[EmbodiedMemoryEvent],
    margin_m: float,
) -> list[list[EmbodiedMemoryEvent]]:
    remaining = set(range(len(events)))
    clusters: list[list[EmbodiedMemoryEvent]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            current_pose = events[current].pose
            assert current_pose is not None
            for candidate in sorted(remaining):
                candidate_pose = events[candidate].pose
                assert candidate_pose is not None
                distance = math.hypot(
                    current_pose.x - candidate_pose.x,
                    current_pose.y - candidate_pose.y,
                )
                threshold = (
                    margin_m
                    + current_pose.uncertainty_radius_m
                    + candidate_pose.uncertainty_radius_m
                )
                if distance <= threshold:
                    remaining.remove(candidate)
                    component.add(candidate)
                    frontier.append(candidate)
        clusters.append([events[index] for index in sorted(component)])
    return clusters


def _observation_layer(event: EmbodiedMemoryEvent) -> str:
    layer_name = event.payload.get("layer_name")
    if isinstance(layer_name, str) and layer_name.strip():
        return layer_name.strip()
    observation_kind = event.payload.get("observation_kind")
    if isinstance(observation_kind, str) and observation_kind.strip():
        return observation_kind.strip()
    if event.provenance is not None and event.provenance.sensor_id:
        return f"sensor:{event.provenance.sensor_id}"
    return event.source_type


def _claims(event: EmbodiedMemoryEvent) -> list[_Claim]:
    if not event.robot_id:
        return []
    claims: list[_Claim] = []
    if event.event_type == "safety_decision":
        decision = event.payload.get("decision")
        target_floor = event.payload.get("target_floor")
        intent = event.payload.get("intent")
        if isinstance(decision, str) and decision and target_floor is not None:
            claims.append(_claim(
                event,
                key=f"safety_decision:{intent or 'unknown'}:floor={target_floor}",
                value=decision,
                scope_key=f"floor={target_floor}",
            ))

    assertions = event.payload.get("safety_assertions")
    if isinstance(assertions, list):
        for assertion in assertions:
            if not isinstance(assertion, dict) or "value" not in assertion:
                continue
            subject = assertion.get("subject")
            predicate = assertion.get("predicate")
            if not isinstance(subject, str) or not subject.strip():
                continue
            if not isinstance(predicate, str) or not predicate.strip():
                continue
            scope = assertion.get("scope")
            scope_key = _canonical(scope) if scope is not None else None
            claims.append(_claim(
                event,
                key=f"assertion:{subject.strip()}:{predicate.strip()}:{scope_key or 'spatial'}",
                value=assertion["value"],
                scope_key=scope_key,
            ))

    if event.event_type == "observation" and event.payload.get("observation_kind") == "environment_state":
        floor = event.pose.floor if event.pose is not None else None
        hazards = event.payload.get("hazards")
        if floor is not None and isinstance(hazards, list):
            for hazard in hazards:
                if isinstance(hazard, str) and hazard:
                    claims.append(_claim(
                        event,
                        key=f"hazard:{hazard}:floor={floor}",
                        value="present",
                        scope_key=f"floor={floor}",
                    ))
        victims = event.payload.get("victims_by_floor")
        if isinstance(victims, dict):
            for victim_floor, count in victims.items():
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    claims.append(_claim(
                        event,
                        key=f"victim_count:floor={victim_floor}",
                        value=count,
                        scope_key=f"floor={victim_floor}",
                    ))
    return claims


def _claim(
    event: EmbodiedMemoryEvent,
    *,
    key: str,
    value: Any,
    scope_key: str | None,
) -> _Claim:
    return _Claim(
        key=key,
        value=value,
        value_key=_canonical(value),
        event_id=event.event_id,
        robot_id=event.robot_id or "",
        observed_at=event.observed_at,
        scope_key=scope_key,
        pose=event.pose,
        mission_shared_frame=event.payload.get("spatial_frame_scope") == "mission",
    )


def _claims_are_spatially_comparable(
    left: _Claim,
    right: _Claim,
    margin_m: float,
) -> bool:
    if (
        left.pose is not None
        and right.pose is not None
        and left.mission_shared_frame
        and right.mission_shared_frame
    ):
        if left.pose.frame_id != right.pose.frame_id:
            return False
        if left.pose.floor and right.pose.floor and left.pose.floor != right.pose.floor:
            return False
        distance = math.hypot(left.pose.x - right.pose.x, left.pose.y - right.pose.y)
        return distance <= (
            margin_m
            + left.pose.uncertainty_radius_m
            + right.pose.uncertainty_radius_m
        )
    return bool(left.scope_key and left.scope_key == right.scope_key)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _empty_assessment(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "agreement_count": 0,
        "contradiction_count": 0,
        "agreements": [],
        "contradictions": [],
        "compared_robot_ids": [],
        "compared_event_ids": [],
        "advisory_only": True,
        "requires_current_state_revalidation": True,
    }
