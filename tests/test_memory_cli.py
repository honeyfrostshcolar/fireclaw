from __future__ import annotations

import json
from pathlib import Path

from fireclaw_core.memory_cli import main as memory_cli_main


def test_memory_cli_indexes_jsonl_and_runs_eval(tmp_path: Path):
    memory_path = tmp_path / "memory.jsonl"
    index_path = tmp_path / "memory.sqlite"
    fixture_path = tmp_path / "cases.json"

    # Write a memory record
    memory_path.write_text(
        '{"record_id":"successful-rescue-floor-2","mission_id":"m1","record_type":"mission_outcome","content":{"command":"去二楼救人","status":"succeeded"},"created_at":"2026-06-11T00:00:00+00:00"}\n',
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
    assert memory_cli_main(["eval", "--index-path", str(index_path), "--fixture", str(fixture_path), "--threshold", "1.0"]) == 0
