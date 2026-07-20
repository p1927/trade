#!/usr/bin/env python3
"""Fail if trade_integrations call sites import requests outside http/."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations" / "trade_integrations"
HTTP_PKG = INTEGRATIONS / "http"

IMPORT_RE = re.compile(r"^\s*import\s+requests\b")
FROM_RE = re.compile(r"^\s*from\s+requests\b")
CALL_RE = re.compile(r"\brequests\.(get|post|Session|put|delete|patch|head)\b")

ALLOWED_IMPORT_FILES = frozenset(
    {
        HTTP_PKG / "gateway.py",
    }
)

SKIP_LINE_SUBSTRINGS = (
    "curl_cffi",
    "requests as cffi_requests",
    "Do not import",
    "Don't:",
    "requests.get(...)",
)


def _scan_file(path: Path) -> list[str]:
    if path.is_relative_to(HTTP_PKG):
        return []
    if path in ALLOWED_IMPORT_FILES:
        return []
    rel = path.relative_to(ROOT)
    hits: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{rel}: unreadable ({exc})"]
    for lineno, line in enumerate(text.splitlines(), start=1):
        if any(skip in line for skip in SKIP_LINE_SUBSTRINGS):
            continue
        if IMPORT_RE.search(line) or FROM_RE.search(line):
            hits.append(f"{rel}:{lineno}: {line.strip()}")
        elif CALL_RE.search(line):
            hits.append(f"{rel}:{lineno}: {line.strip()}")
    return hits


def main() -> int:
    if not INTEGRATIONS.is_dir():
        print(f"Missing integrations tree: {INTEGRATIONS}", file=sys.stderr)
        return 2

    violations: list[str] = []
    for path in sorted(INTEGRATIONS.rglob("*.py")):
        violations.extend(_scan_file(path))

    if violations:
        print("HTTP gateway violations (use trade_integrations.http instead):", file=sys.stderr)
        for item in violations:
            print(f"  {item}", file=sys.stderr)
        return 1

    print("OK: no bare requests usage under integrations/trade_integrations (except http/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
