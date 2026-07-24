"""Read helpers for observability JSONL files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_jsonl_tail(path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    """Return up to ``limit`` most recent JSON objects from a JSONL file."""
    if not path.is_file() or limit <= 0:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
        if len(rows) >= limit:
            break
    rows.reverse()
    return rows


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
