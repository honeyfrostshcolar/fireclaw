from __future__ import annotations

from dataclasses import dataclass, field

from fireclaw_core.sensors.discovery import SensorDiscoveryReport, SensorMappingRule


@dataclass(frozen=True)
class DiscoveryDiff:
    status: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    stale_confirmation: bool = False
    fingerprint_status: str | None = None
    fingerprint_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "added": list(self.added),
            "removed": list(self.removed),
            "changed": list(self.changed),
            "confirmed": list(self.confirmed),
            "unconfirmed": list(self.unconfirmed),
            "stale_confirmation": self.stale_confirmation,
        }
        if self.fingerprint_status is not None:
            payload["fingerprint_status"] = self.fingerprint_status
        if self.fingerprint_reason is not None:
            payload["fingerprint_reason"] = self.fingerprint_reason
        return payload


def build_discovery_diff(
    report: SensorDiscoveryReport,
    profile_rules: tuple[SensorMappingRule, ...],
) -> DiscoveryDiff:
    runtime = {
        (finding.topic, finding.message_type): finding
        for finding in report.findings
        if finding.status in {"verified", "degraded"}
    }
    profile = {
        (rule.topic_pattern, rule.message_type): rule
        for rule in profile_rules
    }
    added = sorted(topic for (topic, _message_type) in runtime.keys() - profile.keys())
    removed = sorted(topic for (topic, _message_type) in profile.keys() - runtime.keys())
    changed: list[str] = []
    confirmed: list[str] = []
    unconfirmed: list[str] = []
    for key in sorted(runtime.keys() & profile.keys()):
        finding = runtime[key]
        rule = profile[key]
        if finding.sensor != rule.sensor:
            changed.append(finding.topic)
        elif rule.confirmed:
            confirmed.append(finding.topic)
        else:
            unconfirmed.append(finding.topic)
    comparison = report.fingerprint_comparison
    fingerprint_status = comparison.status if comparison is not None else None
    fingerprint_reason = comparison.reason if comparison is not None else None
    stale_confirmation = fingerprint_status in {"missing", "stale", "unknown"}
    if unconfirmed:
        status = "needs_confirmation"
    elif not added and not removed and not changed and not stale_confirmation:
        status = "fresh"
    else:
        status = "changed"
    return DiscoveryDiff(
        status=status,
        added=added,
        removed=removed,
        changed=changed,
        confirmed=confirmed,
        unconfirmed=unconfirmed,
        stale_confirmation=stale_confirmation,
        fingerprint_status=fingerprint_status,
        fingerprint_reason=fingerprint_reason,
    )


def render_confirmed_discovery_blocks(
    *,
    report: SensorDiscoveryReport,
    message_timeout_seconds: float,
    confirmed_by: str,
    confirmed_at: str,
) -> str:
    lines: list[str] = []
    if report.runtime_fingerprint is not None:
        fingerprint = report.runtime_fingerprint
        lines.extend([
            "[robot.discovery_fingerprint]",
            f'source = "{_toml_escape(fingerprint.source)}"',
            f'topics_hash = "{_toml_escape(fingerprint.topics_hash)}"',
        ])
        if fingerprint.nodes_hash is not None:
            lines.append(f'nodes_hash = "{_toml_escape(fingerprint.nodes_hash)}"')
        lines.extend([
            f'confirmed_by = "{_toml_escape(confirmed_by)}"',
            f'confirmed_at = "{_toml_escape(confirmed_at)}"',
            "",
        ])
    lines.extend([
        "[robot.sensor_discovery]",
        "enabled = true",
        f"message_timeout_seconds = {message_timeout_seconds:.1f}",
        "",
    ])
    for finding in report.findings:
        if finding.status != "verified":
            continue
        lines.extend([
            "[[robot.sensor_discovery.rules]]",
            f'topic_pattern = "{_toml_escape(finding.topic)}"',
            f'message_type = "{_toml_escape(finding.message_type)}"',
            f'sensor = "{_toml_escape(finding.sensor)}"',
            f"confidence = {finding.confidence:.2f}",
            "confirmed = true",
            f'confirmed_by = "{_toml_escape(confirmed_by)}"',
            f'confirmed_at = "{_toml_escape(confirmed_at)}"',
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def replace_robot_table_block(existing: str, table_header: str, replacement_lines: list[str]) -> str:
    lines = existing.splitlines()
    result: list[str] = []
    index = 0
    found = False
    while index < len(lines):
        if lines[index].strip() == table_header:
            found = True
            result.extend(replacement_lines)
            index += 1
            while index < len(lines):
                stripped = lines[index].strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    if table_header == "[robot.sensor_discovery]" and stripped == "[[robot.sensor_discovery.rules]]":
                        index += 1
                        while index < len(lines):
                            nested = lines[index].strip()
                            if nested.startswith("[") and nested.endswith("]"):
                                break
                            index += 1
                        continue
                    break
                index += 1
            continue
        result.append(lines[index])
        index += 1
    if not found:
        result.extend(["", *replacement_lines])
    return "\n".join(result).rstrip() + "\n"


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
