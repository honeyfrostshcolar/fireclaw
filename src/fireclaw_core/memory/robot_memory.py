from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fireclaw_core.agent.robot import EnvironmentState, RobotState
from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryProducer,
    SpatialMemoryContext,
)


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fireclaw_core.memory.entity_extraction import EntityExtractionPipeline


@dataclass(frozen=True)
class RobotMemorySnapshot:
    body_state_event_id: str | None = None
    environment_event_id: str | None = None
    sensor_event_ids: tuple[str, ...] = ()

    @property
    def evidence_event_ids(self) -> tuple[str, ...]:
        return tuple(
            event_id
            for event_id in (
                self.body_state_event_id,
                self.environment_event_id,
                *self.sensor_event_ids,
            )
            if event_id is not None
        )


class RobotMemoryRecorder:
    """Writes adapter state and explicit sensor observations to embodied memory."""

    def __init__(
        self,
        *,
        robot_producer: EmbodiedMemoryProducer,
        sensor_producer: EmbodiedMemoryProducer,
        runtime_mode: str,
        entity_extraction_pipeline: EntityExtractionPipeline | None = None,
    ) -> None:
        if robot_producer.producer_type != "robot_adapter":
            raise ValueError("robot_producer must use producer_type robot_adapter")
        if sensor_producer.producer_type != "sensor_adapter":
            raise ValueError("sensor_producer must use producer_type sensor_adapter")
        if runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError(
                f"Invalid runtime_mode: {runtime_mode}. "
                f"Must be one of: {sorted(MEMORY_RUNTIME_MODES)}"
            )
        self._robot_producer = robot_producer
        self._sensor_producer = sensor_producer
        self._runtime_mode = runtime_mode
        self._entity_extraction_pipeline = entity_extraction_pipeline

    def record_snapshot(
        self,
        *,
        mission_id: str,
        robot_state: RobotState | None,
        environment_state: EnvironmentState | None,
        subtask_id: str | None = None,
        observed_at: str | None = None,
    ) -> RobotMemorySnapshot:
        timestamp = observed_at or datetime.now(timezone.utc).isoformat()
        body_state_event_id: str | None = None
        sensor_event_ids: tuple[str, ...] = ()
        environment_event_id: str | None = None
        if robot_state is not None:
            body_state_event_id = self._record_body_state(
                mission_id, robot_state, subtask_id, timestamp
            )
            sensor_event_ids = self._record_sensor_findings(
                mission_id, robot_state, subtask_id, timestamp
            )
        if environment_state is not None:
            environment_event = self._safe_record(
                self._robot_producer,
                mission_id=mission_id,
                event_type="observation",
                evidence_kind="runtime_evidence",
                payload={
                    "observation_kind": "environment_state",
                    "reachable_floors": environment_state.reachable_floors,
                    "hazards": list(environment_state.hazards),
                    "victims_by_floor": dict(environment_state.victims_by_floor),
                },
                source_type="robot_environment_state",
                robot_id=robot_state.robot_id if robot_state is not None else None,
                subtask_id=subtask_id,
                observed_at=timestamp,
                sensitivity="restricted",
            )
            environment_event_id = (
                environment_event.event_id if environment_event is not None else None
            )
        return RobotMemorySnapshot(
            body_state_event_id=body_state_event_id,
            environment_event_id=environment_event_id,
            sensor_event_ids=sensor_event_ids,
        )

    def record_sensor_observation(
        self,
        *,
        mission_id: str,
        robot_id: str,
        sensor_id: str,
        payload: dict[str, Any],
        confidence: float,
        source_type: str,
        subtask_id: str | None = None,
        observed_at: str | None = None,
        pose: SpatialMemoryContext | None = None,
        sensitivity: str = "standard",
    ) -> str | None:
        """Record a measurement without inventing missing confidence or pose."""
        event = self._safe_record(
            self._sensor_producer,
            mission_id=mission_id,
            event_type="observation",
            evidence_kind="sensor_evidence",
            payload=payload,
            source_type=source_type,
            sensor_id=sensor_id,
            robot_id=robot_id,
            subtask_id=subtask_id,
            observed_at=observed_at,
            pose=pose,
            confidence=confidence,
            sensitivity=sensitivity,
        )
        return event.event_id if event is not None else None

    def _record_body_state(
        self,
        mission_id: str,
        robot_state: RobotState,
        subtask_id: str | None,
        observed_at: str,
    ) -> str | None:
        event = self._safe_record(
            self._robot_producer,
            mission_id=mission_id,
            event_type="body_state",
            evidence_kind="runtime_evidence",
            payload={
                "mode": robot_state.mode,
                "dry_run": robot_state.dry_run,
                "online": robot_state.online,
                "battery_percent": robot_state.battery_percent,
                "current_floor": robot_state.current_floor,
                "available_sensors": robot_state.available_sensors,
                "supports_real_execution": robot_state.supports_real_execution,
            },
            source_type="robot_state",
            robot_id=robot_state.robot_id,
            subtask_id=subtask_id,
            observed_at=observed_at,
            sensitivity="restricted",
        )
        return event.event_id if event is not None else None

    def _record_sensor_findings(
        self,
        mission_id: str,
        robot_state: RobotState,
        subtask_id: str | None,
        observed_at: str,
    ) -> tuple[str, ...]:
        diagnostics = robot_state.sensor_diagnostics
        findings = diagnostics.get("findings") if isinstance(diagnostics, dict) else None
        if not isinstance(findings, list):
            return ()
        event_ids: list[str] = []
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            sensor_id = finding.get("sensor")
            confidence = finding.get("confidence")
            if (
                not isinstance(sensor_id, str)
                or not sensor_id
                or isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
            ):
                continue
            event = self._safe_record(
                self._sensor_producer,
                mission_id=mission_id,
                event_type="observation",
                evidence_kind="sensor_evidence",
                payload={"observation_kind": "sensor_discovery", "finding": dict(finding)},
                source_type="sensor_discovery",
                sensor_id=sensor_id,
                robot_id=robot_state.robot_id,
                subtask_id=subtask_id,
                observed_at=observed_at,
                confidence=float(confidence),
            )
            if event is not None:
                event_ids.append(event.event_id)
        return tuple(event_ids)

    def _safe_record(self, producer: EmbodiedMemoryProducer, **kwargs: Any):
        try:
            event = producer.record_event(runtime_mode=self._runtime_mode, **kwargs)
        except Exception:
            logger.warning("Failed to write robot embodied-memory event", exc_info=True)
            return None
        if event.event_type == "observation" and self._entity_extraction_pipeline is not None:
            try:
                report = self._entity_extraction_pipeline.process_observation(event)
                if report.issues:
                    logger.warning(
                        "Entity extraction reported %d issue(s) for observation %s: %s",
                        len(report.issues),
                        event.event_id,
                        [issue.message for issue in report.issues],
                    )
            except Exception:
                logger.warning(
                    "Entity extraction failed for persisted observation %s",
                    event.event_id,
                    exc_info=True,
                )
        return event
