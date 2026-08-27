"""Readline-backed console input for operator consoles.

裸 ``input()`` 在中文宽字符下退格会错位（残留空格），方向键会插入转义
序列且没有历史记录。GNU readline 提供标准行编辑、UTF-8 宽字符正确的
列计算和上下键历史回顾；历史持久化到运行目录，重启后仍可回顾。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_HISTORY_LENGTH = 500


def _load_readline() -> Any | None:
    try:
        import readline
    except ImportError:  # pragma: no cover - platform fallback
        return None
    return readline


class ConsoleInput:
    """Prompt wrapper adding readline history and persistence."""

    def __init__(self, history_path: str | Path | None = None) -> None:
        self._readline = _load_readline()
        self.history_path = (
            Path(history_path) if history_path is not None else None
        )
        if self._readline is not None and self.history_path is not None:
            try:
                self.history_path.parent.mkdir(parents=True, exist_ok=True)
                self._readline.read_history_file(str(self.history_path))
            except OSError:
                pass
            self._readline.set_history_length(_HISTORY_LENGTH)

    @property
    def history_enabled(self) -> bool:
        return self._readline is not None

    def add(self, text: str) -> None:
        if self._readline is not None and text.strip():
            self._readline.add_history(text)

    def prompt(self, text: str) -> str:
        raw = input(text)
        self.add(raw)
        return raw

    def save(self) -> None:
        if self._readline is None or self.history_path is None:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self._readline.write_history_file(str(self.history_path))
        except OSError:
            pass
