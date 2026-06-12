from fireclaw_core.mission.mission_registry import JsonlMissionRegistry, MissionRecord, MissionSubtaskRecord


def test_mission_registry_creates_mission_and_records_subtask(tmp_path):
    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")

    mission = registry.create_mission(
        mission_id="mission-1",
        session_id="operator-a",
        command="搜索二楼和三楼",
        created_at="2026-06-08T01:00:00+00:00",
    )
    registry.record_subtask(
        mission_id="mission-1",
        robot_id="robot-1",
        task_id="task-1",
        command="去二楼搜索",
        status="accepted",
        created_at="2026-06-08T01:00:01+00:00",
    )

    loaded = registry.get_mission("mission-1")

    assert mission == MissionRecord(
        mission_id="mission-1",
        session_id="operator-a",
        command="搜索二楼和三楼",
        status="created",
        created_at="2026-06-08T01:00:00+00:00",
        updated_at="2026-06-08T01:00:00+00:00",
        subtasks=[],
    )
    assert loaded.subtasks == [
        MissionSubtaskRecord(
            robot_id="robot-1",
            task_id="task-1",
            command="去二楼搜索",
            status="accepted",
            created_at="2026-06-08T01:00:01+00:00",
            updated_at="2026-06-08T01:00:01+00:00",
        )
    ]


def test_mission_registry_updates_subtask_status_and_projects_trace(tmp_path):
    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    registry.create_mission(
        mission_id="mission-1",
        session_id="operator-a",
        command="搜索二楼和三楼",
        created_at="2026-06-08T01:00:00+00:00",
    )
    registry.record_subtask(
        mission_id="mission-1",
        robot_id="robot-1",
        task_id="task-1",
        command="去二楼搜索",
        status="accepted",
        created_at="2026-06-08T01:00:01+00:00",
    )
    registry.record_subtask(
        mission_id="mission-1",
        robot_id="robot-2",
        task_id="task-2",
        command="去三楼搜索",
        status="accepted",
        created_at="2026-06-08T01:00:02+00:00",
    )
    registry.update_subtask(
        mission_id="mission-1",
        robot_id="robot-1",
        task_id="task-1",
        status="succeeded",
        updated_at="2026-06-08T01:00:03+00:00",
        result={"status": "succeeded"},
    )

    trace = registry.mission_trace("mission-1")

    assert trace["mission_id"] == "mission-1"
    assert trace["status"] == "running"
    assert trace["subtask_count"] == 2
    assert trace["completed_subtask_count"] == 1
    assert trace["subtasks"][0]["status"] == "succeeded"
    assert trace["subtasks"][0]["result"] == {"status": "succeeded"}


def test_mission_registry_marks_mission_failed_when_any_subtask_fails(tmp_path):
    registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    registry.create_mission(
        mission_id="mission-1",
        session_id="operator-a",
        command="搜索二楼",
        created_at="2026-06-08T01:00:00+00:00",
    )
    registry.record_subtask(
        mission_id="mission-1",
        robot_id="robot-1",
        task_id="task-1",
        command="去二楼搜索",
        status="accepted",
        created_at="2026-06-08T01:00:01+00:00",
    )
    registry.update_subtask(
        mission_id="mission-1",
        robot_id="robot-1",
        task_id="task-1",
        status="failed",
        updated_at="2026-06-08T01:00:02+00:00",
        error="blocked",
    )

    assert registry.mission_trace("mission-1")["status"] == "failed"
