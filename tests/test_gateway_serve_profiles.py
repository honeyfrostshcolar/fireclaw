from __future__ import annotations

from pathlib import Path

from fireclaw_core.gateway.serve import _ensure_data_dir


def test_ensure_data_dir_skips_robots_template_when_profiles_are_configured(tmp_path: Path) -> None:
    _ensure_data_dir(tmp_path, create_robot_template=False)

    assert tmp_path.exists()
    assert not (tmp_path / "robots.json").exists()
