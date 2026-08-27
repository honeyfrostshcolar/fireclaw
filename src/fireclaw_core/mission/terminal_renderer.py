"""Rich terminal rendering for operator-facing Agent activity streams.

The renderer deliberately accepts semantic events instead of pre-styled ANSI
fragments.  This mirrors the useful part of OpenClaw's TUI boundary: producers
describe whether an item is reasoning, a tool interaction, or final content;
the terminal surface decides how prominent that item should be.

Only concise reasoning *status* is rendered.  Private chain-of-thought is not
exposed through this interface.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from rich.console import Console
from rich.status import Status
from rich.text import Text


__all__ = ["TerminalAgentRenderer", "sanitize_terminal_text"]

ToolPhase = Literal["called", "ran", "explored"]
RenderTone = Literal["primary", "success", "warning", "error"]

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)
_RENDER_LOCK = threading.RLock()
_TOOL_PHASE_LABELS: dict[ToolPhase, str] = {
    "called": "Called",
    "ran": "Ran",
    "explored": "Explored",
}
_PRIMARY_STYLES: dict[RenderTone, str] = {
    "primary": "bold bright_white",
    "success": "bold bright_green",
    "warning": "bold bright_yellow",
    "error": "bold bright_red",
}
_PRIMARY_BODY_STYLES: dict[RenderTone, str] = {
    "primary": "bright_white",
    "success": "bright_green",
    "warning": "bright_yellow",
    "error": "bright_red",
}


def sanitize_terminal_text(value: Any) -> str:
    """Strip remote terminal controls while retaining ordinary line breaks."""

    text = _ANSI_ESCAPE_RE.sub("", str(value or ""))
    return "".join(
        character
        if character in {"\n", "\t"} or ord(character) >= 32
        else " "
        for character in text.replace("\r", " ")
    ).strip()


class TerminalAgentRenderer:
    """Render a hierarchical Agent activity stream with Rich.

    ``render_thought`` and ``render_tool_call`` are secondary trace rows and
    therefore dim. ``render_final_response`` is primary, bold content.  The
    complete event ledger remains in the backend; this class only controls the
    operator-facing visual hierarchy.
    """

    def __init__(
        self,
        *,
        console: Console | None = None,
        stream: Any | None = None,
        force_terminal: bool | None = None,
        width: int | None = None,
    ) -> None:
        if console is not None and stream is not None:
            raise ValueError("Pass either console or stream, not both.")
        self.console = console or Console(
            file=stream or sys.stdout,
            force_terminal=force_terminal,
            color_system=(
                "standard"
                if force_terminal is True
                else None
                if force_terminal is False
                else "auto"
            ),
            highlight=False,
            markup=False,
            width=width,
        )
        self.stream = self.console.file
        self._lock = _RENDER_LOCK

    @property
    def is_interactive(self) -> bool:
        return bool(self.console.is_terminal)

    def render_thought(
        self,
        message: Any,
        *,
        label: str = "Thought",
        details: Iterable[Any] = (),
    ) -> None:
        """Render concise reasoning progress as a low-prominence trace row."""

        body = self._lines(message, details)
        self._render_activity(
            f"• {sanitize_terminal_text(label) or 'Thought'}",
            body,
            header_style="dim bright_black",
            body_style="dim",
        )

    def render_tool_call(
        self,
        name: str,
        *,
        phase: ToolPhase = "called",
        arguments: Mapping[str, Any] | None = None,
        summary: Any | None = None,
        output: Any | None = None,
        details: Iterable[Any] = (),
        failed: bool = False,
    ) -> None:
        """Render a tool invocation or environment result as secondary trace."""

        phase_label = _TOOL_PHASE_LABELS.get(phase, str(phase).title())
        body: list[str] = []
        if summary is not None:
            body.extend(self._lines(summary))
        if arguments is not None:
            body.append("Arguments: " + self._compact_value(arguments))
        if output is not None:
            body.append("Output: " + self._compact_value(output))
        body.extend(self._lines(None, details))
        style = "dim red" if failed else "dim"
        self._render_activity(
            f"• {phase_label} {sanitize_terminal_text(name) or 'tool'}",
            body,
            header_style=style,
            body_style=style,
        )

    def render_primary_status(
        self,
        title: str,
        lines: Iterable[Any] = (),
        *,
        tone: RenderTone = "primary",
    ) -> None:
        """Render an operator-relevant status without marking it as final."""

        resolved_tone = tone if tone in _PRIMARY_STYLES else "primary"
        self._render_activity(
            f"• {sanitize_terminal_text(title) or 'FireClaw'}",
            self._lines(None, lines),
            header_style=_PRIMARY_STYLES[resolved_tone],
            body_style=_PRIMARY_BODY_STYLES[resolved_tone],
        )

    def render_final_response(
        self,
        message: Any,
        *,
        title: str | None = None,
        tone: RenderTone = "primary",
    ) -> None:
        """Render the final or otherwise decisive operator-facing response."""

        resolved_tone = tone if tone in _PRIMARY_STYLES else "primary"
        message_lines = self._lines(message)
        if title is None and message_lines:
            header = f"• {message_lines.pop(0)}"
        else:
            header = f"• {sanitize_terminal_text(title) or 'Final'}"
        self._render_activity(
            header,
            message_lines,
            header_style=_PRIMARY_STYLES[resolved_tone],
            body_style=_PRIMARY_STYLES[resolved_tone],
        )

    def status(self, message: Any, *, label: str = "Working") -> Status:
        """Create a transient dim Rich status row for ongoing work."""

        return self.console.status(
            self._status_text(message, label=label),
            spinner="dots",
            spinner_style="dim bright_black",
        )

    @staticmethod
    def update_status(
        status: Status,
        message: Any,
        *,
        label: str = "Working",
    ) -> None:
        status.update(
            TerminalAgentRenderer._status_text(message, label=label)
        )

    @staticmethod
    def _status_text(message: Any, *, label: str) -> Text:
        safe_label = sanitize_terminal_text(label) or "Working"
        safe_message = sanitize_terminal_text(message)
        suffix = f" · {safe_message}" if safe_message else ""
        return Text(f"• {safe_label}{suffix}", style="dim")

    def _render_activity(
        self,
        header: str,
        lines: list[str],
        *,
        header_style: str,
        body_style: str,
    ) -> None:
        # Ask Rich to wrap each content cell, then prepend a fixed branch to
        # every visual line. This provides a hanging indent without the padded
        # trailing spaces produced by an expanded Table in captured logs.
        document = Text()

        def append_row(
            prefix: str,
            content: str,
            *,
            prefix_style: str,
            content_style: str,
        ) -> None:
            available_width = max(1, self.console.width - len(prefix))
            wrapped = Text(content, style=content_style).wrap(
                self.console,
                available_width,
                overflow="fold",
            )
            visual_lines = list(wrapped) or [Text("", style=content_style)]
            for index, visual_line in enumerate(visual_lines):
                rendered_prefix = prefix if index == 0 else " " * len(prefix)
                document.append(rendered_prefix, style=prefix_style)
                document.append_text(visual_line)
                document.append("\n")

        safe_header = sanitize_terminal_text(header)
        if safe_header.startswith("• "):
            header_prefix = "• "
            header_content = safe_header[2:]
        else:
            header_prefix = ""
            header_content = safe_header
        append_row(
            header_prefix,
            header_content,
            prefix_style=header_style,
            content_style=header_style,
        )
        for index, line in enumerate(lines):
            branch = "└─ " if index == len(lines) - 1 else "├─ "
            append_row(
                f"  {branch}",
                line,
                prefix_style="dim bright_black",
                content_style=body_style,
            )
        document.append("\n")
        with self._lock:
            self.console.print(document, end="", soft_wrap=False)

    @staticmethod
    def _lines(
        message: Any | None,
        details: Iterable[Any] = (),
    ) -> list[str]:
        lines: list[str] = []
        values: list[Any] = []
        if message is not None:
            values.append(message)
        if isinstance(details, (str, bytes)):
            values.append(details)
        else:
            values.extend(details)
        for value in values:
            safe = sanitize_terminal_text(value)
            lines.extend(safe.splitlines() or ([""] if safe == "" else []))
        return lines

    @staticmethod
    def _compact_value(value: Any, *, maximum_characters: int = 600) -> str:
        if isinstance(value, str):
            rendered = sanitize_terminal_text(value)
        else:
            try:
                rendered = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            except (TypeError, ValueError):
                rendered = sanitize_terminal_text(value)
        if len(rendered) <= maximum_characters:
            return rendered
        return rendered[: maximum_characters - 1] + "…"
