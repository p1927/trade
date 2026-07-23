"""Parse YAML frontmatter from LLM-Wiki raw source markdown exports."""

from __future__ import annotations

import re
from pathlib import Path

_FM_FIELD_RE = re.compile(r"^([A-Za-z0-9_]+):[ \t]*([^\n]*?)[ \t]*$", re.MULTILINE)


def parse_frontmatter_text(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[3:end]
    out: dict[str, str] = {}
    for match in _FM_FIELD_RE.finditer(block):
        key = match.group(1).strip()
        raw = match.group(2).strip()
        if not raw:
            continue
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            raw = raw[1:-1]
        out[key] = raw
    return out


def read_frontmatter(md_path: Path) -> dict[str, str]:
    if not md_path.is_file():
        return {}
    try:
        return parse_frontmatter_text(md_path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def frontmatter_field(md_path: Path, field: str) -> str:
    return str(read_frontmatter(md_path).get(field) or "").strip()
