"""Post-ready validation sidecar for proof artifacts.

Follows OpenClaw's sidecar pattern: an explicit, optional helper that
runs validation checks and writes a redacted report.  Not auto-run on
gateway startup — robot validation should be triggered explicitly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fireclaw_core.log_redaction import redact_dict


@dataclass
class ValidationSidecar:
    """Thin wrapper around a callable that produces a validation report.

    Parameters
    ----------
    output_dir:
        Directory where ``validation-report.json`` will be written.
        Created automatically if it does not exist.
    run:
        Callable that receives *output_dir* and returns a JSON-serialisable
        dict describing the validation results.
    """

    output_dir: Path
    run: Callable[[Path], dict[str, Any]]

    def run_once(self) -> dict[str, Any]:
        """Execute the validation callable, redact secrets, write report."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        report = redact_dict(self.run(self.output_dir))
        (self.output_dir / "validation-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report
