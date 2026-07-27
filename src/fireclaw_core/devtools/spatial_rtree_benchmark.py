"""Benchmark SQLite R*Tree spatial candidates against linear scan semantics."""
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fireclaw_core.memory.embodied_memory import (
    EmbodiedMemoryStore,
    SpatialMemoryContext,
)
from fireclaw_core.memory.mission_memory_facade import (
    MemoryAccessContext,
    MissionMemoryFacade,
)


T0 = "2026-07-27T10:00:00+00:00"


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    frame_id: str
    floor: str
    x: float
    y: float
    z: float | None
    radius_m: float
    memory_types: tuple[str, ...]


DEFAULT_CASES = (
    BenchmarkCase(
        name="nearest_2d_floor_scoped",
        frame_id="map",
        floor="2",
        x=50.0,
        y=50.0,
        z=None,
        radius_m=15.0,
        memory_types=("observation", "gist"),
    ),
    BenchmarkCase(
        name="nearest_3d_fail_closed",
        frame_id="map",
        floor="2",
        x=50.0,
        y=50.0,
        z=2.0,
        radius_m=15.0,
        memory_types=("observation", "gist"),
    ),
)


def run_benchmark(
    *,
    sizes: tuple[int, ...],
    iterations: int,
    warmup: int,
    limit: int,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="fireclaw-spatial-bench-") as directory:
        root = Path(directory)
        for size in sizes:
            store = _build_store(root / f"size-{size}", size)
            facade = MissionMemoryFacade(store=store, runtime_mode="simulation")
            access = MemoryAccessContext(
                mission_id="mission-1",
                runtime_mode="simulation",
                requester_id="benchmark",
                scopes=frozenset({"memory.restricted.read"}),
            )
            for case in DEFAULT_CASES:
                results.append(
                    _benchmark_case(
                        store=store,
                        facade=facade,
                        access=access,
                        case=case,
                        iterations=iterations,
                        warmup=warmup,
                        limit=limit,
                        dataset_size=size,
                    )
                )
    return {
        "benchmark": "spatial_rtree_equivalence",
        "sizes": list(sizes),
        "iterations": iterations,
        "warmup": warmup,
        "results": results,
    }


def _benchmark_case(
    *,
    store: EmbodiedMemoryStore,
    facade: MissionMemoryFacade,
    access: MemoryAccessContext,
    case: BenchmarkCase,
    iterations: int,
    warmup: int,
    limit: int,
    dataset_size: int,
) -> dict[str, Any]:
    def query() -> dict[str, Any]:
        return facade.query_nearest(
            access,
            frame_id=case.frame_id,
            floor=case.floor,
            x=case.x,
            y=case.y,
            z=case.z,
            max_distance_m=case.radius_m,
            memory_types=case.memory_types,
            limit=limit,
            reference_at=T0,
        )

    assert store.index is not None
    store.index._rtree_available = True
    indexed = query()
    store.index._rtree_available = False
    linear = query()
    equivalent = _equivalence_signature(indexed) == _equivalence_signature(linear)

    indexed_ms = _time_query(query, store=store, use_rtree=True, warmup=warmup, iterations=iterations)
    linear_ms = _time_query(query, store=store, use_rtree=False, warmup=warmup, iterations=iterations)
    store.index._rtree_available = True
    return {
        "dataset_size": dataset_size,
        "query_type": case.name,
        "result_count": len(indexed["results"]),
        "equivalent": equivalent,
        "rtree_backend": indexed["candidate_backend"],
        "linear_backend": linear["candidate_backend"],
        "rtree_ms": _summary(indexed_ms),
        "linear_ms": _summary(linear_ms),
        "speedup_p50": (
            _percentile(linear_ms, 50) / _percentile(indexed_ms, 50)
            if indexed_ms and _percentile(indexed_ms, 50) > 0
            else None
        ),
    }


def _time_query(
    query: Any,
    *,
    store: EmbodiedMemoryStore,
    use_rtree: bool,
    warmup: int,
    iterations: int,
) -> list[float]:
    assert store.index is not None
    store.index._rtree_available = use_rtree
    for _ in range(warmup):
        query()
    timings: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        query()
        timings.append((time.perf_counter() - started) * 1000.0)
    return timings


def _build_store(root: Path, count: int) -> EmbodiedMemoryStore:
    root.mkdir(parents=True, exist_ok=True)
    store = EmbodiedMemoryStore(
        root / "memory.jsonl",
        index_path=root / "memory.sqlite",
    )
    for index in range(count):
        floor = "2" if index % 3 else "1"
        frame_id = "map" if index % 7 else "robot-local"
        x = float((index * 17) % 100)
        y = float((index * 31) % 100)
        z = 2.0 if index % 5 else None
        event_type = "gist" if index % 10 == 0 else "observation"
        payload: dict[str, Any] = {"sequence": index}
        if event_type == "gist":
            payload["spatial_geometries"] = [
                {
                    "frame_id": frame_id,
                    "floor": floor,
                    "center_x": x,
                    "center_y": y,
                    "center_z": z,
                    "radius_m": 1.0 + float(index % 4),
                }
            ]
        store.record_event(
            event_id=f"event-{index:07d}",
            mission_id="mission-1",
            event_type=event_type,
            payload=payload,
            runtime_mode="simulation",
            source_type="benchmark",
            observed_at=T0,
            pose=SpatialMemoryContext(
                frame_id=frame_id,
                floor=floor,
                x=x,
                y=y,
                z=z,
                uncertainty_radius_m=float(index % 3),
            ),
        )
    return store


def _equivalence_signature(result: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            item["memory_type"],
            item["record_id"],
            round(float(item["center_distance_m"]), 6),
            round(float(item["distance_to_uncertainty_m"]), 6),
            item["spatial_match"]["source"],
        )
        for item in result["results"]
    )


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "min": round(min(values), 4),
        "p50": round(_percentile(values, 50), 4),
        "p95": round(_percentile(values, 95), 4),
        "p99": round(_percentile(values, 99), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.fmean(values), 4),
    }


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark FireClaw R*Tree spatial retrieval against linear scan.",
    )
    parser.add_argument(
        "--sizes",
        default="1000",
        help="Comma-separated dataset sizes, e.g. 1000,10000,100000.",
    )
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    sizes = tuple(int(item.strip()) for item in args.sizes.split(",") if item.strip())
    if not sizes or any(size <= 0 for size in sizes):
        raise SystemExit("--sizes must contain positive integers")
    if args.iterations <= 0 or args.warmup < 0 or args.limit <= 0:
        raise SystemExit("--iterations and --limit must be positive; --warmup >= 0")
    print(
        json.dumps(
            run_benchmark(
                sizes=sizes,
                iterations=args.iterations,
                warmup=args.warmup,
                limit=args.limit,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
