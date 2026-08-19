"""FireClaw Configuration Diff and Physical Impact Evaluation Engine."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Optional

from fireclaw_core.config.templates import _dict_to_toml
from fireclaw_core.infra import tomllib_compat as tomllib


@dataclass
class DiffField:
    """Represents a single configuration field difference and its physical impact."""
    path: str
    old_val: Any = None
    new_val: Any = None
    change_type: str = "modified"  # "added" | "modified" | "removed"
    impact_level: str = "info"     # "info" | "warning" | "critical"
    impact_description_zh: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "old_val": self.old_val,
            "new_val": self.new_val,
            "change_type": self.change_type,
            "impact_level": self.impact_level,
            "impact_description_zh": self.impact_description_zh,
        }


@dataclass
class ConfigDiffResult:
    """Summary and field-level diff results with evaluated physical impacts."""
    has_changes: bool = False
    diff_fields: list[DiffField] = field(default_factory=list)
    impact_summary_zh: list[str] = field(default_factory=list)
    raw_diff_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_changes": self.has_changes,
            "diff_fields": [f.to_dict() for f in self.diff_fields],
            "impact_summary_zh": list(self.impact_summary_zh),
            "raw_diff_text": self.raw_diff_text,
        }


class ConfigDiffEngine:
    """Computes side-by-side configuration differences and assesses physical robotics impact."""

    def evaluate_impact(
        self,
        path: str,
        old_val: Any,
        new_val: Any,
        change_type: str,
    ) -> tuple[str, str]:
        """Evaluate the physical impact level and description for a changed field.

        Returns (impact_level, impact_description_zh).
        """
        path_lower = path.lower()

        # 1. Mode change (simulation <-> real) -> CRITICAL
        if path_lower == "robot.mode" or path_lower.endswith(".mode") or path_lower == "mode":
            return "critical", "模式变更：实机与仿真环境隔离，实机启动前将强制安全门禁"

        # 2. Navigation / Chassis interface change -> WARNING
        nav_keywords = ["move_base", "cmd_vel", "navigation_action", "nav2", "navigation"]
        if any(k in path_lower for k in nav_keywords) or (
            isinstance(old_val, str) and any(k in old_val.lower() for k in nav_keywords)
        ) or (
            isinstance(new_val, str) and any(k in new_val.lower() for k in nav_keywords)
        ):
            return "warning", "底盘移动/导航接口变更：将影响 Navigation Plugin 动作下发"

        # 3. Actuator change -> WARNING
        actuator_keywords = ["water_cannon", "gimbal", "actuator", "cannon", "arm", "pump"]
        if any(k in path_lower for k in actuator_keywords):
            return "warning", "执行机构变更：请确保现场安全与机构处于初始安全复位状态"

        # 4. Sensor topic change -> WARNING
        sensor_keywords = ["laser", "thermal", "gas", "camera", "pointcloud", "imu", "scan", "odom", "sensor"]
        if any(k in path_lower for k in sensor_keywords):
            return "warning", "传感器话题变更：请确保对应 ROS 节点已发布有效数据流"

        # 5. Default / General field change -> INFO
        action_zh = "新增" if change_type == "added" else ("删除" if change_type == "removed" else "修改")
        return "info", f"配置字段变更：{path} ({action_zh})"

    def compare_configs(
        self,
        old_config_dict: dict[str, Any],
        new_config_dict: dict[str, Any],
    ) -> ConfigDiffResult:
        """Compare two configuration dictionaries and generate diff with physical impact assessment."""
        diff_fields: list[DiffField] = []

        def _traverse(old_node: Any, new_node: Any, current_path: str) -> None:
            if isinstance(old_node, dict) and isinstance(new_node, dict):
                all_keys = sorted(set(old_node.keys()) | set(new_node.keys()))
                for k in all_keys:
                    p = f"{current_path}.{k}" if current_path else k
                    if k in old_node and k not in new_node:
                        # Removed
                        _collect_removed(old_node[k], p)
                    elif k in new_node and k not in old_node:
                        # Added
                        _collect_added(new_node[k], p)
                    else:
                        # Both exist
                        _traverse(old_node[k], new_node[k], p)
            elif old_node != new_node:
                level, desc = self.evaluate_impact(current_path, old_node, new_node, "modified")
                diff_fields.append(
                    DiffField(
                        path=current_path,
                        old_val=old_node,
                        new_val=new_node,
                        change_type="modified",
                        impact_level=level,
                        impact_description_zh=desc,
                    )
                )

        def _collect_added(node: Any, path: str) -> None:
            if isinstance(node, dict) and node:
                for k, v in sorted(node.items()):
                    _collect_added(v, f"{path}.{k}")
            else:
                level, desc = self.evaluate_impact(path, None, node, "added")
                diff_fields.append(
                    DiffField(
                        path=path,
                        old_val=None,
                        new_val=node,
                        change_type="added",
                        impact_level=level,
                        impact_description_zh=desc,
                    )
                )

        def _collect_removed(node: Any, path: str) -> None:
            if isinstance(node, dict) and node:
                for k, v in sorted(node.items()):
                    _collect_removed(v, f"{path}.{k}")
            else:
                level, desc = self.evaluate_impact(path, node, None, "removed")
                diff_fields.append(
                    DiffField(
                        path=path,
                        old_val=node,
                        new_val=None,
                        change_type="removed",
                        impact_level=level,
                        impact_description_zh=desc,
                    )
                )

        _traverse(old_config_dict, new_config_dict, "")

        # Format raw unified diff
        old_toml = _dict_to_toml(old_config_dict)
        new_toml = _dict_to_toml(new_config_dict)
        raw_diff_lines = list(
            difflib.unified_diff(
                old_toml.splitlines(keepends=True),
                new_toml.splitlines(keepends=True),
                fromfile="old_config.toml",
                tofile="new_config.toml",
            )
        )
        raw_diff_text = "".join(raw_diff_lines)

        has_changes = len(diff_fields) > 0

        # Build impact summary sorted by severity: critical -> warning -> info
        priority_order = {"critical": 0, "warning": 1, "info": 2}
        sorted_fields = sorted(diff_fields, key=lambda f: priority_order.get(f.impact_level, 99))
        seen_summaries: set[str] = set()
        impact_summary_zh: list[str] = []
        for df in sorted_fields:
            if df.impact_description_zh and df.impact_description_zh not in seen_summaries:
                seen_summaries.add(df.impact_description_zh)
                impact_summary_zh.append(df.impact_description_zh)

        return ConfigDiffResult(
            has_changes=has_changes,
            diff_fields=diff_fields,
            impact_summary_zh=impact_summary_zh,
            raw_diff_text=raw_diff_text,
        )

    def compare_toml_strings(self, old_toml: str, new_toml: str) -> ConfigDiffResult:
        """Compare two TOML configuration strings."""
        old_dict = tomllib.loads(old_toml) if old_toml.strip() else {}
        new_dict = tomllib.loads(new_toml) if new_toml.strip() else {}

        result = self.compare_configs(old_dict, new_dict)

        # Generate exact unified diff from the provided raw TOML strings
        raw_diff_lines = list(
            difflib.unified_diff(
                old_toml.splitlines(keepends=True),
                new_toml.splitlines(keepends=True),
                fromfile="old_config.toml",
                tofile="new_config.toml",
            )
        )
        result.raw_diff_text = "".join(raw_diff_lines)
        return result
