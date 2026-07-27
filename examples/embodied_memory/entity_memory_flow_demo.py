from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from fireclaw_core.memory.embodied_memory import (
    EmbodiedMemoryProducer,
    EmbodiedMemoryStore,
    SpatialMemoryContext,
)
from fireclaw_core.memory.entity_extraction import EntityExtractionPipeline
from fireclaw_core.memory.entity_memory import EntityMemoryService, FireClawEntity
from fireclaw_core.memory.mission_memory_facade import (
    MemoryAccessContext,
    MissionMemoryFacade,
)
from fireclaw_core.memory.mission_memory_tools import MissionMemoryTools
from fireclaw_core.memory.mission_memory_tools import (
    QUERY_ENTITIES_TOOL,
    QUERY_IDENTITY_PROPOSALS_TOOL,
    QUERY_NEAREST_TOOL,
)


MISSION_ID = "demo-mission-001"
RUNTIME_MODE = "simulation"
T0 = "2026-07-20T10:00:00+00:00"
T1 = "2026-07-20T10:01:00+00:00"
T2 = "2026-07-20T10:02:00+00:00"
T3 = "2026-07-20T10:03:00+00:00"


def main() -> None:
    verbose_json = "--json" in sys.argv[1:]
    with tempfile.TemporaryDirectory(prefix="fireclaw-entity-demo-") as workspace:
        root = Path(workspace)
        store = EmbodiedMemoryStore(
            root / "embodied-memory.jsonl",
            index_path=root / "embodied-memory.sqlite",
        )
        entity_memory = EntityMemoryService(
            store=store,
            resolver_producer=EmbodiedMemoryProducer(
                store,
                producer_type="entity_resolver",
                producer_id="demo-entity-resolver",
            ),
            operator_producer=EmbodiedMemoryProducer(
                store,
                producer_type="approval_runtime",
                producer_id="demo-operator-approval",
            ),
            runtime_mode=RUNTIME_MODE,
        )
        extraction = EntityExtractionPipeline(
            store=store,
            entity_memory=entity_memory,
            runtime_mode=RUNTIME_MODE,
        )
        facade = MissionMemoryFacade(
            store=store,
            runtime_mode=RUNTIME_MODE,
            entity_memory=entity_memory,
        )
        tools = MissionMemoryTools(facade)
        access = MemoryAccessContext(
            mission_id=MISSION_ID,
            runtime_mode=RUNTIME_MODE,
            requester_id="demo-operator",
            scopes=frozenset({"memory.restricted.read"}),
        )

        print("\nFireClaw Entity Memory 最小端到端 demo")
        print(f"临时工作目录: {root}")
        print("说明: 这是 dry-run，不连接 ROS、不调用 LLM、不控制真实机器人。")
        print("默认输出是人类可读摘要；需要完整字段时可加参数: --json")

        step(
            1,
            "robot-A 写入一条结构化 Observation: 二楼东走廊发现疑似 victim。",
        )
        obs_a = record_victim_observation(
            store,
            event_id="obs-robot-a-victim",
            robot_id="robot-A",
            track_id="7",
            x=12.0,
            y=6.0,
            observed_at=T0,
        )
        print_observation_summary(obs_a)
        print_optional_json(verbose_json, summarize_observation(obs_a))

        step(
            2,
            "EntityExtractionPipeline 只读取 payload.entities，生成 entity_mention。",
        )
        report_a = extraction.process_observation(obs_a)
        print(
            "生成结果: "
            f"{len(report_a.mention_event_ids)} 个 entity_mention, "
            f"{len(report_a.entity_ids)} 个当前 entity, "
            f"{len(report_a.issues)} 个问题。"
        )
        print(f"Observation -> Mention: {report_a.observation_event_id} -> {report_a.mention_event_ids[0]}")
        print_optional_json(verbose_json, {
            "observation_event_id": report_a.observation_event_id,
            "mention_event_ids": list(report_a.mention_event_ids),
            "entity_ids": list(report_a.entity_ids),
            "issues": [issue.message for issue in report_a.issues],
        })

        step(
            3,
            "机器人/mission agent 通过只读工具查询二楼当前 victim entity。",
        )
        entity_query = tools.execute(
                QUERY_ENTITIES_TOOL,
                {"entity_kind": "victim", "floor": "2"},
                access=access,
        )
        print_entity_query_summary(entity_query)
        print_optional_tool_json(
            verbose_json,
            entity_query,
            keys=("count", "retrieval_mode", "entities"),
        )

        step(
            4,
            "robot-B 在相近位置也发现同名 victim，但 tracker ID 是本机局部 ID。",
        )
        obs_b = record_victim_observation(
            store,
            event_id="obs-robot-b-victim",
            robot_id="robot-B",
            track_id="3",
            x=12.5,
            y=6.2,
            observed_at=T1,
        )
        report_b = extraction.process_observation(obs_b)
        print_observation_summary(obs_b)
        print(
            "生成结果: "
            f"{len(report_b.mention_event_ids)} 个 entity_mention, "
            f"{len(report_b.entity_ids)} 个当前 entity。"
        )
        print_optional_json(verbose_json, {
            "robot_b_observation": summarize_observation(obs_b),
            "robot_b_entity_ids": list(report_b.entity_ids),
            "robot_b_mention_event_ids": list(report_b.mention_event_ids),
        })

        step(
            5,
            "系统生成跨机器人 identity proposal，但不会自动 merge。",
        )
        proposal_result = tools.execute(
            QUERY_IDENTITY_PROPOSALS_TOOL,
            {"entity_kind": "victim"},
            access=access,
        )
        print_identity_proposal_summary(proposal_result)
        print_optional_tool_json(
            verbose_json,
            proposal_result,
            keys=("count", "automatic_merge", "operator_confirmation_required", "proposals"),
        )
        proposal = proposal_result["proposals"][0]

        step(
            6,
            "模拟 operator 确认两个 track 是同一个 victim，写入 entity_resolution merge。",
        )
        resolution_id = entity_memory.apply_operator_resolution(
            mission_id=MISSION_ID,
            action="merge",
            entity_ids=tuple(proposal["entity_ids"]),
            target_entity_id=proposal["entity_ids"][0],
            operator_id="demo-operator",
            reason="demo operator confirmed both robots observed the same victim",
        )
        print(f"写入 operator resolution: action=merge, event_id={resolution_id}")
        print("含义: 这不是覆盖旧记录，而是追加一条人工确认事件。")
        print_optional_json(verbose_json, {"entity_resolution_event_id": resolution_id, "action": "merge"})

        step(
            7,
            "再次查询 entity projection: 两个 mention 已经投影成同一个当前 victim。",
        )
        entities_after_merge = entity_memory.list_entities(
            mission_id=MISSION_ID,
            entity_kind="victim",
        )
        print_entities_after_merge_summary(entities_after_merge)
        print_optional_json(verbose_json, [summarize_entity(entity) for entity in entities_after_merge])

        step(
            8,
            "写入一个二楼 hazard 和一个多区域 Gist，再查机器人附近 5 米内的记忆。",
        )
        record_hazard_observation(store)
        record_multi_area_gist(store)
        nearest = tools.execute(
            QUERY_NEAREST_TOOL,
            {
                "frame_id": "building-map",
                "floor": "2",
                "x": 10.0,
                "y": 5.5,
                "max_distance_m": 5.0,
                "memory_types": ["entity", "observation", "gist"],
                "entity_kinds": ["victim"],
                "limit": 10,
            },
            access=access,
        )
        print_nearest_summary(nearest)
        print_optional_tool_json(
            verbose_json,
            nearest,
            keys=(
                "count",
                "retrieval_mode",
                "candidate_backend",
                "requires_current_state_revalidation",
                "results",
            ),
        )

        step(
            9,
            "把 memory 结果变成规划上下文: 只能 advisory，后续动作仍要看当前传感器和 SafetyGate。",
        )
        planner_context = {
            "operator_command": "去二楼刚才发现的被困人员附近搜索",
            "memory_context_for_planner": [
                {
                    "memory_type": item["memory_type"],
                    "record_id": item["record_id"],
                    "distance_to_uncertainty_m": item["distance_to_uncertainty_m"],
                    "evidence_event_ids": item["evidence_event_ids"],
                }
                for item in nearest["results"]
            ],
            "planner_should_do": [
                "navigate near the remembered victim area",
                "re-check current thermal/camera evidence",
                "avoid nearby hazard/gist regions",
                "pass SafetyGate before any physical action",
            ],
            "memory_can_authorize_action": nearest["safety"]["can_authorize_action"],
        }
        print_planner_context_summary(planner_context)
        print_optional_json(verbose_json, planner_context)


def record_victim_observation(
    store: EmbodiedMemoryStore,
    *,
    event_id: str,
    robot_id: str,
    track_id: str,
    x: float,
    y: float,
    observed_at: str,
):
    return store.record_event(
        event_id=event_id,
        mission_id=MISSION_ID,
        event_type="observation",
        payload={
            "summary": "thermal detector found a possible person",
            "spatial_frame_scope": "mission",
            "entities": [
                {
                    "name": "victim-alpha",
                    "entity_kind": "victim",
                    "confidence": 0.87,
                    "source_track_namespace": "thermal-camera",
                    "source_track_id": track_id,
                    "pose": {
                        "frame_id": "building-map",
                        "floor": "2",
                        "x": x,
                        "y": y,
                        "uncertainty_radius_m": 1.0,
                    },
                    "attributes": {"detector_class": "person"},
                }
            ],
        },
        runtime_mode=RUNTIME_MODE,
        source_type="thermal_detector",
        robot_id=robot_id,
        observed_at=observed_at,
        pose=SpatialMemoryContext(frame_id="building-map", floor="2", x=x - 1.0, y=y),
        confidence=0.87,
    )


def record_hazard_observation(store: EmbodiedMemoryStore) -> None:
    store.record_event(
        event_id="obs-hazard-hotspot",
        mission_id=MISSION_ID,
        event_type="observation",
        payload={"hazard": "thermal hotspot near east corridor"},
        runtime_mode=RUNTIME_MODE,
        source_type="thermal_detector",
        robot_id="robot-A",
        observed_at=T2,
        pose=SpatialMemoryContext(
            frame_id="building-map",
            floor="2",
            x=15.5,
            y=5.5,
            uncertainty_radius_m=2.0,
        ),
        confidence=0.82,
    )


def record_multi_area_gist(store: EmbodiedMemoryStore) -> None:
    store.record_event(
        event_id="gist-smoke-east-corridor",
        mission_id=MISSION_ID,
        event_type="gist",
        payload={
            "summary": "smoke was observed near the second-floor east corridor and stairwell",
            "spatial_geometries": [
                {
                    "frame_id": "building-map",
                    "floor": "2",
                    "center_x": 9.0,
                    "center_y": 5.5,
                    "radius_m": 1.5,
                },
                {
                    "frame_id": "building-map",
                    "floor": "2",
                    "center_x": 18.0,
                    "center_y": 7.0,
                    "radius_m": 2.5,
                },
            ],
        },
        runtime_mode=RUNTIME_MODE,
        source_type="memory_consolidation",
        robot_id="robot-A",
        observed_at=T3,
        confidence=0.8,
    )


def summarize_observation(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "robot_id": event.robot_id,
        "payload.entities": event.payload.get("entities", []),
    }


def summarize_entity(entity: FireClawEntity) -> dict[str, Any]:
    pose = entity.current_pose.to_dict() if entity.current_pose is not None else None
    return {
        "entity_id": entity.entity_id,
        "kind": entity.entity_kind,
        "aliases": list(entity.aliases),
        "status": entity.status,
        "source_robot_ids": list(entity.source_robot_ids),
        "tracking_identities": list(entity.tracking_identities),
        "mention_event_ids": list(entity.mention_event_ids),
        "observation_event_ids": list(entity.observation_event_ids),
        "current_pose": pose,
    }


def print_observation_summary(event: Any) -> None:
    entity = event.payload["entities"][0]
    pose = entity["pose"]
    print(
        f"{event.robot_id} observation={event.event_id}: "
        f"{entity['entity_kind']} '{entity['name']}' "
        f"at {pose['frame_id']} floor={pose['floor']} "
        f"({pose['x']}, {pose['y']}), "
        f"uncertainty={pose['uncertainty_radius_m']}m, "
        f"confidence={entity['confidence']}, "
        f"tracker={entity['source_track_namespace']}:{entity['source_track_id']}"
    )


def print_entity_query_summary(result: dict[str, Any]) -> None:
    print(f"查询结果: 找到 {result['count']} 个 victim entity。")
    for entity in result["entities"]:
        pose = entity["current_pose"]
        print(
            f"- entity={short_id(entity['entity_id'])}, "
            f"status={entity['status']}, "
            f"pose=floor {pose['floor']} ({pose['x']}, {pose['y']}), "
            f"robots={entity['source_robot_ids']}, "
            f"tracker_ids={entity['tracking_identities']}"
        )
        print(
            "  证据链: "
            f"mentions={short_ids(entity['mention_event_ids'])}, "
            f"observations={entity['observation_event_ids']}"
        )
    print_safety_summary(result)


def print_identity_proposal_summary(result: dict[str, Any]) -> None:
    print(
        f"候选数量: {result['count']}; "
        f"automatic_merge={result['automatic_merge']}; "
        f"operator_confirmation_required={result['operator_confirmation_required']}"
    )
    for proposal in result["proposals"]:
        print(
            f"- proposal={short_id(proposal['proposal_id'])}: "
            f"{short_ids(proposal['entity_ids'])} 可能是同一个 "
            f"{proposal['entity_kind']}"
        )
        print(
            f"  距离={proposal['center_distance_m']:.2f}m, "
            f"允许阈值={proposal['uncertainty_overlap_threshold_m']:.2f}m, "
            f"时间差={proposal['time_gap_seconds']:.0f}s, "
            f"robots={proposal['source_robot_ids']}"
        )
        print(f"  原因: {', '.join(proposal['reason_codes'])}")
    print_safety_summary(result)


def print_entities_after_merge_summary(entities: list[FireClawEntity]) -> None:
    print(f"当前 victim entity 数量: {len(entities)}")
    for entity in entities:
        pose = entity.current_pose
        pose_text = "unknown"
        if pose is not None:
            pose_text = f"floor {pose.floor} ({pose.x}, {pose.y}), uncertainty={pose.uncertainty_radius_m}m"
        print(
            f"- entity={short_id(entity.entity_id)} 已汇总 "
            f"{len(entity.mention_event_ids)} 个 mention / "
            f"{len(entity.observation_event_ids)} 个 observation"
        )
        print(f"  当前位置: {pose_text}")
        print(f"  来源机器人: {list(entity.source_robot_ids)}")
        print(f"  tracker identities: {list(entity.tracking_identities)}")
        print(f"  observation evidence: {list(entity.observation_event_ids)}")


def print_nearest_summary(result: dict[str, Any]) -> None:
    print(
        f"nearest 查询返回 {result['count']} 条结果; "
        f"mode={result['retrieval_mode']}; "
        f"backend={result.get('candidate_backend', 'unknown')}; "
        f"requires_revalidation={result['requires_current_state_revalidation']}"
    )
    for index, item in enumerate(result["results"], start=1):
        print(
            f"{index}. {item['memory_type']} {item['record_id']}: "
            f"center={item['center_distance_m']:.2f}m, "
            f"conservative={item['distance_to_uncertainty_m']:.2f}m, "
            f"evidence={short_ids(item['evidence_event_ids'])}"
        )
        if item["memory_type"] == "gist":
            geometry = item.get("matched_spatial_geometry", {})
            print(
                "   命中 Gist 区域: "
                f"center=({geometry.get('center_x')}, {geometry.get('center_y')}), "
                f"radius={geometry.get('radius_m')}m"
            )
        if item["record_id"] == "obs-hazard-hotspot":
            print(
                "   hazard 中心距离超过 5m，但 uncertainty=2m，"
                "所以保守边界距离是 3.5m，仍被附近查询命中。"
            )
    print_safety_summary(result)


def print_planner_context_summary(context: dict[str, Any]) -> None:
    print(f"operator command: {context['operator_command']}")
    print("planner 将看到的精简 memory context:")
    for item in context["memory_context_for_planner"]:
        print(
            f"- {item['memory_type']} {item['record_id']}: "
            f"conservative_distance={item['distance_to_uncertainty_m']:.2f}m, "
            f"evidence={short_ids(item['evidence_event_ids'])}"
        )
    print("planner 应该采取的原则:")
    for action in context["planner_should_do"]:
        print(f"- {action}")
    print(f"memory_can_authorize_action={context['memory_can_authorize_action']}")


def print_safety_summary(result: dict[str, Any]) -> None:
    safety = result.get("safety") or {}
    print(
        "安全标记: "
        f"advisory_only={result.get('advisory_only')}, "
        f"requires_revalidation={result.get('requires_current_state_revalidation')}, "
        f"can_authorize_action={safety.get('can_authorize_action')}"
    )


def print_optional_tool_json(
    enabled: bool,
    result: dict[str, Any],
    *,
    keys: tuple[str, ...],
) -> None:
    if not enabled:
        return
    print("完整 JSON:")
    print_json({key: result.get(key) for key in keys})
    print_json({
        "advisory_only": result.get("advisory_only"),
        "requires_current_state_revalidation": result.get(
            "requires_current_state_revalidation"
        ),
        "safety": result.get("safety"),
    })


def print_optional_json(enabled: bool, value: Any) -> None:
    if enabled:
        print("完整 JSON:")
        print_json(value)


def step(number: int, title: str) -> None:
    print(f"\n[{number}] {title}")


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def short_id(value: str) -> str:
    if len(value) <= 18:
        return value
    return f"{value[:10]}...{value[-6:]}"


def short_ids(values: list[str] | tuple[str, ...]) -> list[str]:
    return [short_id(value) for value in values]


if __name__ == "__main__":
    main()
