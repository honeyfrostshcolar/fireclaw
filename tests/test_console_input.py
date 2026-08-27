"""Tests for the readline-backed operator console input."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fireclaw_core.mission.console_input import ConsoleInput


class _FakeReadline:
    def __init__(self) -> None:
        self.history: list[str] = []
        self.loaded_from: str | None = None
        self.saved_to: str | None = None
        self.length: int | None = None

    def read_history_file(self, path: str) -> None:
        self.loaded_from = path

    def write_history_file(self, path: str) -> None:
        self.saved_to = path

    def add_history(self, text: str) -> None:
        self.history.append(text)

    def set_history_length(self, length: int) -> None:
        self.length = length


def test_console_input_records_nonempty_lines(monkeypatch, tmp_path: Path) -> None:
    fake = _FakeReadline()
    monkeypatch.setattr(
        "fireclaw_core.mission.console_input._load_readline",
        lambda: fake,
    )
    console = ConsoleInput(history_path=tmp_path / ".cli-history")

    assert console.history_enabled is True
    assert fake.loaded_from == str(tmp_path / ".cli-history")

    console.add("导航到 (0.63, 0.54)")
    console.add("   ")
    assert fake.history == ["导航到 (0.63, 0.54)"]

    console.save()
    assert fake.saved_to == str(tmp_path / ".cli-history")


def test_console_input_degrades_without_readline(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "fireclaw_core.mission.console_input._load_readline",
        lambda: None,
    )
    console = ConsoleInput(history_path=tmp_path / "h")
    assert console.history_enabled is False
    # Must not raise without readline.
    console.add("x")
    console.save()


def test_console_input_survives_missing_history_file(monkeypatch, tmp_path) -> None:
    fake = _FakeReadline()

    def raise_oserror(_path: str) -> None:
        raise OSError("missing")

    fake.read_history_file = raise_oserror  # type: ignore[method-assign]
    monkeypatch.setattr(
        "fireclaw_core.mission.console_input._load_readline",
        lambda: fake,
    )
    console = ConsoleInput(history_path=tmp_path / "nope")
    assert console.history_enabled is True
