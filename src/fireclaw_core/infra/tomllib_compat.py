"""Compatibility module for tomllib (Python 3.11+) with fallback to tomli or pure-Python TOML parser."""
from __future__ import annotations

import ast
import re
import sys
from typing import Any, BinaryIO, TextIO

if sys.version_info >= (3, 11):
    import tomllib as _tomllib
    loads = _tomllib.loads
    load = _tomllib.load
    TOMLDecodeError = _tomllib.TOMLDecodeError
else:
    try:
        import tomli as _tomli  # type: ignore[no-redef]
        loads = _tomli.loads
        load = _tomli.load
        TOMLDecodeError = _tomli.TOMLDecodeError
    except ImportError:
        class TOMLDecodeError(ValueError):  # type: ignore[no-redef]
            pass

        def _strip_comments_and_whitespace(line: str) -> str:
            in_quote = False
            quote_char = ""
            for idx, char in enumerate(line):
                if char in ('"', "'") and (idx == 0 or line[idx - 1] != "\\"):
                    if not in_quote:
                        in_quote = True
                        quote_char = char
                    elif quote_char == char:
                        in_quote = False
                elif char == "#" and not in_quote:
                    return line[:idx].strip()
            return line.strip()

        def _parse_value(val_str: str) -> Any:
            val_str = val_str.strip()
            if val_str.lower() == "true":
                return True
            if val_str.lower() == "false":
                return False
            if (val_str.startswith('"""') and val_str.endswith('"""')) or (val_str.startswith("'''") and val_str.endswith("'''")):
                return val_str[3:-3]
            if (val_str.startswith('"') and val_str.endswith('"')) or (val_str.startswith("'") and val_str.endswith("'")):
                return val_str[1:-1]
            if val_str.startswith("[") and val_str.endswith("]"):
                try:
                    return ast.literal_eval(val_str)
                except Exception:
                    inner = val_str[1:-1].strip()
                    if not inner:
                        return []
                    parts: list[str] = []
                    cur: list[str] = []
                    depth = 0
                    in_q = False
                    q_c = ""
                    for c in inner:
                        if c in ('"', "'") and (not cur or cur[-1] != "\\"):
                            if not in_q:
                                in_q = True
                                q_c = c
                            elif q_c == c:
                                in_q = False
                        elif not in_q:
                            if c in ("[", "{"):
                                depth += 1
                            elif c in ("]", "}"):
                                depth -= 1
                            elif c == "," and depth == 0:
                                parts.append("".join(cur).strip())
                                cur = []
                                continue
                        cur.append(c)
                    if cur:
                        parts.append("".join(cur).strip())
                    return [_parse_value(p) for p in parts if p]
            if val_str.startswith("{") and val_str.endswith("}"):
                try:
                    return ast.literal_eval(val_str)
                except Exception:
                    return {}
            try:
                if "." in val_str or "e" in val_str.lower():
                    return float(val_str)
                return int(val_str)
            except ValueError:
                return val_str

        def loads(s: str) -> dict[str, Any]:
            if isinstance(s, bytes):
                s = s.decode("utf-8")
            result: dict[str, Any] = {}
            current_table = result

            raw_lines = s.splitlines()
            combined_lines: list[tuple[int, str]] = []
            accumulated: list[str] = []
            start_line_idx = 1
            bracket_depth = 0
            in_multiline_str = False

            for idx, raw_line in enumerate(raw_lines, 1):
                stripped = _strip_comments_and_whitespace(raw_line)
                if not stripped and bracket_depth == 0:
                    continue

                if bracket_depth == 0 and not accumulated:
                    start_line_idx = idx

                # Track brackets outside quotes
                in_quote = False
                quote_char = ""
                for char_idx, char in enumerate(stripped):
                    if char in ('"', "'") and (char_idx == 0 or stripped[char_idx - 1] != "\\"):
                        if not in_quote:
                            in_quote = True
                            quote_char = char
                        elif quote_char == char:
                            in_quote = False
                    elif not in_quote:
                        if char in ("[", "{"):
                            bracket_depth += 1
                        elif char in ("]", "}"):
                            bracket_depth = max(0, bracket_depth - 1)

                accumulated.append(stripped)
                if bracket_depth == 0:
                    combined_lines.append((start_line_idx, " ".join(accumulated)))
                    accumulated = []

            if accumulated:
                combined_lines.append((start_line_idx, " ".join(accumulated)))

            for line_idx, line in combined_lines:
                line = line.strip()
                if not line:
                    continue

                if line.startswith("[") and line.endswith("]"):
                    header = line[1:-1].strip()
                    if not header:
                        raise TOMLDecodeError(f"Empty table header at line {line_idx}")
                    keys = [k.strip() for k in header.split(".")]
                    current_table = result
                    for k in keys:
                        if k not in current_table or not isinstance(current_table[k], dict):
                            current_table[k] = {}
                        current_table = current_table[k]
                    continue

                if "=" in line:
                    k_part, v_part = line.split("=", 1)
                    key = k_part.strip()
                    current_table[key] = _parse_value(v_part.strip())
                else:
                    raise TOMLDecodeError(f"Invalid TOML line at {line_idx}: {line}")

            return result

        def load(fp: BinaryIO | TextIO) -> dict[str, Any]:
            content = fp.read()
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            return loads(content)

__all__ = ["load", "loads", "TOMLDecodeError"]
