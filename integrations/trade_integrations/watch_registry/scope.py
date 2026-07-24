"""Symbol scope and Nautilus owner id helpers for the watch registry."""

from __future__ import annotations

from typing import Any

OWNER_KIND_SESSION = "session"
OWNER_KIND_AUTONOMOUS = "autonomous_agent"
SESSION_OWNER_PREFIX = "ws_"


def nautilus_owner_id(*, owner_kind: str, owner_id: str) -> str:
    """Map registry owner to Nautilus registry / handoff agent_id."""
    owner_id = str(owner_id or "").strip()
    if not owner_id:
        raise ValueError("owner_id required")
    kind = str(owner_kind or "").strip().lower()
    if kind == OWNER_KIND_AUTONOMOUS:
        return owner_id
    if kind == OWNER_KIND_SESSION:
        if owner_id.startswith(SESSION_OWNER_PREFIX):
            return owner_id
        return f"{SESSION_OWNER_PREFIX}{owner_id}"
    raise ValueError(f"unsupported owner_kind: {owner_kind}")


def parse_nautilus_owner_id(nautilus_owner: str) -> tuple[str, str]:
    """Return (owner_kind, owner_id) from Nautilus handoff/registry id."""
    nautilus_owner = str(nautilus_owner or "").strip()
    if nautilus_owner.startswith(SESSION_OWNER_PREFIX):
        return OWNER_KIND_SESSION, nautilus_owner[len(SESSION_OWNER_PREFIX) :]
    if nautilus_owner.startswith("aa_"):
        return OWNER_KIND_AUTONOMOUS, nautilus_owner
    return OWNER_KIND_AUTONOMOUS, nautilus_owner


def is_session_nautilus_owner(nautilus_owner: str) -> bool:
    return str(nautilus_owner or "").startswith(SESSION_OWNER_PREFIX)


def symbols_for_watch(watch: dict[str, Any]) -> tuple[str, ...]:
    """Symbols referenced by a watch record."""
    syms: set[str] = set()
    for raw in watch.get("symbols") or []:
        sym = str(raw).strip().upper()
        if sym:
            syms.add(sym)
    spec = watch.get("watch_spec") if isinstance(watch.get("watch_spec"), dict) else {}
    for row in spec.get("rules") or []:
        if isinstance(row, dict):
            sym = str(row.get("symbol") or "").strip().upper()
            if sym:
                syms.add(sym)
    return tuple(sorted(syms))


def resolve_watch_scope_from_agent(agent: dict[str, Any]) -> tuple[str, ...]:
    from trade_integrations.execution.routing_context import resolve_agent_routing

    routing = resolve_agent_routing(agent)
    return tuple(routing.watch_symbols)


def symbols_for_owner(nautilus_owner: str, *, watches: list[dict[str, Any]] | None = None) -> tuple[str, ...]:
    if watches is None:
        from trade_integrations.watch_registry.store import list_watches_for_nautilus_owner

        watches = list_watches_for_nautilus_owner(nautilus_owner)
    syms: set[str] = set()
    for watch in watches:
        if str(watch.get("status") or "active") != "active":
            continue
        syms.update(symbols_for_watch(watch))
    return tuple(sorted(syms))


def union_symbols_for_owners(nautilus_owners: list[str]) -> tuple[str, ...]:
    syms: set[str] = set()
    for owner in nautilus_owners:
        syms.update(symbols_for_owner(owner))
    return tuple(sorted(syms))


def combined_watch_spec_for_owner(
    nautilus_owner: str,
    *,
    watches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge active watch rules for one Nautilus owner into a single watch_spec."""
    if watches is None:
        from trade_integrations.watch_registry.store import list_watches_for_nautilus_owner

        watches = list_watches_for_nautilus_owner(nautilus_owner)
    rules: list[dict[str, Any]] = []
    cooldown = 300
    gate_minutes: int | None = None
    for watch in watches:
        if str(watch.get("status") or "active") != "active":
            continue
        spec = watch.get("watch_spec") if isinstance(watch.get("watch_spec"), dict) else {}
        cooldown = int(spec.get("cooldown_sec") or watch.get("cooldown_sec") or cooldown)
        gate = spec.get("gate") if isinstance(spec.get("gate"), dict) else {}
        raw_minutes = gate.get("skip_if_unchanged_minutes")
        if raw_minutes is not None:
            minutes = int(raw_minutes)
            gate_minutes = minutes if gate_minutes is None else min(gate_minutes, minutes)
        for row in spec.get("rules") or []:
            if isinstance(row, dict) and row.get("symbol"):
                rules.append(dict(row))
    payload: dict[str, Any] = {
        "rules": rules,
        "cooldown_sec": cooldown,
        "review_triggers": ["watch_rule_fired", "thesis_break", "news_material"],
    }
    if gate_minutes is not None:
        payload["gate"] = {"skip_if_unchanged_minutes": gate_minutes}
    return payload
