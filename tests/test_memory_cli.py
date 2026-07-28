from __future__ import annotations

import json
from pathlib import Path

from fireclaw_core.memory.memory_cli import main as memory_cli_main


def test_memory_cli_indexes_jsonl_and_runs_eval(tmp_path: Path):
    memory_path = tmp_path / "memory.jsonl"
    index_path = tmp_path / "memory.sqlite"
    fixture_path = tmp_path / "cases.json"

    # Write a memory record
    memory_path.write_text(
        '{"record_id":"successful-rescue-floor-2","mission_id":"m1","record_type":"mission_outcome","content":{"command":"去二楼救人","status":"succeeded","_embodied":{"runtime_mode":"real","sensitivity":"standard"}},"created_at":"2026-06-11T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    # Write eval fixture
    fixture_path.write_text(
        '[{"query":"去二楼救人","expected_record_ids":["successful-rescue-floor-2"],"min_score":0.4,"record_type":"mission_outcome"}]',
        encoding="utf-8",
    )

    # Index
    assert memory_cli_main(["index", "--memory-path", str(memory_path), "--index-path", str(index_path)]) == 0
    assert index_path.exists()

    # Eval
    assert memory_cli_main([
        "eval",
        "--index-path",
        str(index_path),
        "--fixture",
        str(fixture_path),
        "--mission-id",
        "m1",
        "--threshold",
        "1.0",
    ]) == 0


def test_memory_cli_index_removes_stale_records(tmp_path: Path):
    """Regression: rebuild() must remove records no longer in JSONL."""
    memory_path = tmp_path / "memory.jsonl"
    index_path = tmp_path / "memory.sqlite"
    fixture_path = tmp_path / "cases.json"

    # Step 1: Index a record
    memory_path.write_text(
        '{"record_id":"old-record","mission_id":"m1","record_type":"mission_outcome","content":{"command":"旧任务","status":"succeeded","_embodied":{"runtime_mode":"real","sensitivity":"standard"}},"created_at":"2026-06-11T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    assert memory_cli_main(["index", "--memory-path", str(memory_path), "--index-path", str(index_path)]) == 0

    # Step 2: Replace JSONL with a different record (old one removed)
    memory_path.write_text(
        '{"record_id":"new-record","mission_id":"m2","record_type":"mission_outcome","content":{"command":"新任务","status":"succeeded","_embodied":{"runtime_mode":"real","sensitivity":"standard"}},"created_at":"2026-06-11T01:00:00+00:00"}\n',
        encoding="utf-8",
    )
    assert memory_cli_main(["index", "--memory-path", str(memory_path), "--index-path", str(index_path)]) == 0

    # Step 3: Eval query for old record should fail (stale)
    fixture_path.write_text(
        '[{"query":"旧任务","expected_record_ids":["old-record"],"min_score":0.4}]',
        encoding="utf-8",
    )
    assert memory_cli_main([
        "eval",
        "--index-path",
        str(index_path),
        "--fixture",
        str(fixture_path),
        "--mission-id",
        "m1",
        "--threshold",
        "1.0",
    ]) == 2

    # Step 4: Eval query for new record should succeed
    fixture_path.write_text(
        '[{"query":"新任务","expected_record_ids":["new-record"],"min_score":0.4}]',
        encoding="utf-8",
    )
    assert memory_cli_main([
        "eval",
        "--index-path",
        str(index_path),
        "--fixture",
        str(fixture_path),
        "--mission-id",
        "m2",
        "--threshold",
        "1.0",
    ]) == 0
