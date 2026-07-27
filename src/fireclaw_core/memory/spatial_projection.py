"""Pure geometry projection helpers for SQLite R*Tree candidate retrieval."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


SPATIAL_PROJECTION_SCHEMA_VERSION = "fireclaw-spatial-projection-v1"
SPATIAL_MEMORY_TYPES = frozenset({"observation", "gist", "entity"})
SPATIAL_GEOMETRY_SOURCES = frozenset({
    "event_pose",
    "conservative_geometry",
    "entity_current_pose",
})


@dataclass(frozen=True)
class SpatialProjectionRow:
    mission_id: str
    runtime_mode: str
    memory_type: str
    source_id: str
    geometry_source: str
    geometry_index: int
    frame_id: str
    floor: str | None
    center_x: float
    center_y: float
    center_z: float | None
    uncertainty_radius_m: float
    min_z: float | None = None
    max_z: float | None = None
    entity_kind: str | None = None
    entity_status: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("mission_id", self.mission_id),
            ("runtime_mode", self.runtime_mode),
            ("source_id", self.source_id),
            ("frame_id", self.frame_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.memory_type not in SPATIAL_MEMORY_TYPES:
            raise ValueError(f"Invalid spatial memory_type: {self.memory_type}")
        if self.geometry_source not in SPATIAL_GEOMETRY_SOURCES:
            raise ValueError(f"Invalid geometry_source: {self.geometry_source}")
        if (
            isinstance(self.geometry_index, bool)
            or not isinstance(self.geometry_index, int)
            or self.geometry_index < 0
        ):
            raise ValueError("geometry_index must be a non-negative integer")
        for name, value in (
            ("center_x", self.center_x),
            ("center_y", self.center_y),
            ("uncertainty_radius_m", self.uncertainty_radius_m),
        ):
            _require_finite(name, value)
        if self.uncertainty_radius_m < 0:
            raise ValueError("uncertainty_radius_m must be >= 0")
        if self.center_z is not None:
            _require_finite("center_z", self.center_z)
        if (self.min_z is None) != (self.max_z is None):
            raise ValueError("min_z and max_z must be provided together")
        if self.min_z is not None and self.max_z is not None:
            _require_finite("min_z", self.min_z)
            _require_finite("max_z", self.max_z)
            if self.min_z > self.max_z:
                raise ValueError("min_z must be <= max_z")

    @property
    def has_z(self) -> bool:
        return self.center_z is not None

    def bounds_2d(self) -> tuple[float, float, float, float]:
        radius = self.uncertainty_radius_m
        return (
            self.center_x - radius,
            self.center_x + radius,
            self.center_y - radius,
            self.center_y + radius,
        )

    def bounds_3d(self) -> tuple[float, float, float, float, float, float] | None:
        if self.center_z is None:
            return None
        min_x, max_x, min_y, max_y = self.bounds_2d()
        if self.min_z is not None and self.max_z is not None:
            min_z, max_z = self.min_z, self.max_z
        elif self.geometry_source == "conservative_geometry":
            min_z = max_z = self.center_z
        else:
            min_z = self.center_z - self.uncertainty_radius_m
            max_z = self.center_z + self.uncertainty_radius_m
        return min_x, max_x, min_y, max_y, min_z, max_z


def event_spatial_projection_rows(record: dict[str, Any]) -> tuple[SpatialProjectionRow, ...]:
    """Project an indexed Observation/Gist record into conservative geometries."""
    memory_type = record.get("record_type")
    if memory_type not in {"observation", "gist"}:
        return ()
    source_id = _nonempty_string(record.get("record_id"))
    mission_id = _nonempty_string(record.get("mission_id"))
    content = record.get("content")
    if source_id is None or mission_id is None or not isinstance(content, dict):
        return ()
    metadata = content.get("_embodied")
    if not isinstance(metadata, dict):
        return ()
    runtime_mode = _nonempty_string(metadata.get("runtime_mode"))
    if runtime_mode is None:
        return ()

    rows: list[SpatialProjectionRow] = []
    pose = metadata.get("pose")
    if isinstance(pose, dict):
        row = _pose_row(
            mission_id=mission_id,
            runtime_mode=runtime_mode,
            memory_type=memory_type,
            source_id=source_id,
            geometry_source="event_pose",
            geometry_index=0,
            pose=pose,
        )
        if row is not None:
            rows.append(row)

    geometries = content.get("spatial_geometries")
    if isinstance(geometries, list):
        for index, geometry in enumerate(geometries):
            if not isinstance(geometry, dict):
                continue
            row = _conservative_geometry_row(
                mission_id=mission_id,
                runtime_mode=runtime_mode,
                memory_type=memory_type,
                source_id=source_id,
                geometry_index=index,
                geometry=geometry,
            )
            if row is not None:
                rows.append(row)
    return tuple(rows)


def entity_spatial_projection_row(
    *,
    mission_id: str,
    runtime_mode: str,
    payload: dict[str, Any],
) -> SpatialProjectionRow | None:
    """Project one current Entity pose without consulting historical locations."""
    entity_id = _nonempty_string(payload.get("entity_id"))
    pose = payload.get("current_pose")
    if entity_id is None or not isinstance(pose, dict):
        return None
    return _pose_row(
        mission_id=mission_id,
        runtime_mode=runtime_mode,
        memory_type="entity",
        source_id=entity_id,
        geometry_source="entity_current_pose",
        geometry_index=0,
        pose=pose,
        entity_kind=_optional_string(payload.get("entity_kind")),
        entity_status=_optional_string(payload.get("status")),
    )


def _pose_row(
    *,
    mission_id: str,
    runtime_mode: str,
    memory_type: str,
    source_id: str,
    geometry_source: str,
    geometry_index: int,
    pose: dict[str, Any],
    entity_kind: str | None = None,
    entity_status: str | None = None,
) -> SpatialProjectionRow | None:
    frame_id = _nonempty_string(pose.get("frame_id"))
    x = _finite_float(pose.get("x"))
    y = _finite_float(pose.get("y"))
    uncertainty = _finite_float(pose.get("uncertainty_radius_m", 0.0))
    if frame_id is None or x is None or y is None or uncertainty is None or uncertainty < 0:
        return None
    z = _finite_float(pose.get("z")) if pose.get("z") is not None else None
    if pose.get("z") is not None and z is None:
        return None
    try:
        return SpatialProjectionRow(
            mission_id=mission_id,
            runtime_mode=runtime_mode,
            memory_type=memory_type,
            source_id=source_id,
            geometry_source=geometry_source,
            geometry_index=geometry_index,
            frame_id=frame_id,
            floor=_optional_string(pose.get("floor")),
            center_x=x,
            center_y=y,
            center_z=z,
            uncertainty_radius_m=uncertainty,
            entity_kind=entity_kind,
            entity_status=entity_status,
        )
    except (TypeError, ValueError):
        return None


def _conservative_geometry_row(
    *,
    mission_id: str,
    runtime_mode: str,
    memory_type: str,
    source_id: str,
    geometry_index: int,
    geometry: dict[str, Any],
) -> SpatialProjectionRow | None:
    frame_id = _nonempty_string(geometry.get("frame_id"))
    center_x = _finite_float(geometry.get("center_x"))
    center_y = _finite_float(geometry.get("center_y"))
    radius = _finite_float(geometry.get("radius_m"))
    if (
        frame_id is None
        or center_x is None
        or center_y is None
        or radius is None
        or radius < 0
    ):
        return None

    center_z = _finite_float(geometry.get("center_z"))
    if geometry.get("center_z") is not None and center_z is None:
        return None
    min_z = max_z = None
    bounds = geometry.get("bounds")
    if isinstance(bounds, dict):
        candidate_min_z = _finite_float(bounds.get("min_z"))
        candidate_max_z = _finite_float(bounds.get("max_z"))
        if candidate_min_z is not None and candidate_max_z is not None:
            if candidate_min_z > candidate_max_z:
                return None
            min_z, max_z = candidate_min_z, candidate_max_z
            if center_z is None:
                center_z = (min_z + max_z) / 2.0
    try:
        return SpatialProjectionRow(
            mission_id=mission_id,
            runtime_mode=runtime_mode,
            memory_type=memory_type,
            source_id=source_id,
            geometry_source="conservative_geometry",
            geometry_index=geometry_index,
            frame_id=frame_id,
            floor=_optional_string(geometry.get("floor")),
            center_x=center_x,
            center_y=center_y,
            center_z=center_z,
            uncertainty_radius_m=radius,
            min_z=min_z,
            max_z=max_z,
        )
    except (TypeError, ValueError):
        return None


def _require_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _nonempty_string(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return _nonempty_string(str(value))
