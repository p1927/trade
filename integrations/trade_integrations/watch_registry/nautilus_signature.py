"""Canonical runtime signature for Nautilus registry agents (restart detection)."""

from __future__ import annotations

import json
from typing import Any


def canonical_watch_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize watch_spec so rule-only edits change the signature."""
    payload = dict(spec or {})
    rules: list[dict[str, Any]] = []
    for row in payload.get("rules") or []:
        if not isinstance(row, dict):
            continue
        normalized = {k: row[k] for k in sorted(row.keys()) if row[k] is not None}
        if normalized.get("symbol"):
            rules.append(normalized)
    rules.sort(
        key=lambda row: (
            str(row.get("symbol") or ""),
            str(row.get("kind") or row.get("type") or ""),
            str(row.get("id") or row.get("rule_id") or ""),
            json.dumps(row, sort_keys=True, separators=(",", ":")),
        )
    )
    triggers = sorted(
        {
            str(item).strip()
            for item in (payload.get("review_triggers") or [])
            if str(item).strip()
        }
    )
    gate_raw = payload.get("gate") if isinstance(payload.get("gate"), dict) else {}
    gate: dict[str, int] = {}
    skip_minutes = gate_raw.get("skip_if_unchanged_minutes")
    if skip_minutes is not None:
        gate["skip_if_unchanged_minutes"] = int(skip_minutes)
    return {
        "rules": rules,
        "cooldown_sec": int(payload.get("cooldown_sec") or 300),
        "review_triggers": triggers,
        "gate": gate,
    }


def agent_runtime_fingerprint(row: dict[str, Any]) -> tuple[str, str, tuple[str, ...], str]:
    agent_id = str(row.get("agent_id") or "").strip()
    market = str(row.get("market") or "IN").strip().upper() or "IN"
    symbols = tuple(
        sorted(str(s).upper() for s in (row.get("symbols") or []) if str(s).strip())
    )
    spec_blob = json.dumps(
        canonical_watch_spec(row.get("watch_spec") if isinstance(row.get("watch_spec"), dict) else {}),
        sort_keys=True,
        separators=(",", ":"),
    )
    return agent_id, market, symbols, spec_blob


def nautilus_registry_signature(agents: list[dict[str, Any]] | None = None) -> tuple[tuple[str, str, tuple[str, ...], str], ...]:
    rows = agents if agents is not None else []
    return tuple(
        sorted(
            agent_runtime_fingerprint(row)
            for row in rows
            if isinstance(row, dict) and row.get("agent_id")
        )
    )
