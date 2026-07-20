"""Lint: application code must not import news stores outside the facade."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOWLIST_PREFIXES = (
    "integrations/trade_integrations/dataflows/news_hub_bridge/",
    "integrations/trade_integrations/hub_storage/",
    "integrations/trade_integrations/dataflows/index_research/news_",
    "integrations/trade_integrations/dataflows/index_research/hub_news_",
    "integrations/trade_integrations/dataflows/hub_wiki/",
    "scripts/distill_hub_news.py",
    "scripts/process_hub_news_staging.py",
    "tests/",
)

FORBIDDEN_MODULES = (
    "trade_integrations.hub_storage.news_events_store",
    "trade_integrations.hub_storage.news_staging_store",
)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _allowed(path: Path) -> bool:
    rel = _rel(path)
    return any(rel == prefix or rel.startswith(prefix) for prefix in ALLOWLIST_PREFIXES)


def _find_forbidden_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_MODULES:
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module in FORBIDDEN_MODULES:
                hits.append(node.module)
    return hits


def test_no_direct_news_store_imports_outside_allowlist() -> None:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        if not path.is_file():
            continue
        if _allowed(path):
            continue
        rel = _rel(path)
        if "node_modules" in rel or ".venv" in rel:
            continue
        for mod in _find_forbidden_imports(path):
            violations.append(f"{rel} imports {mod}")
    assert not violations, "Direct news store imports:\n" + "\n".join(sorted(violations))
