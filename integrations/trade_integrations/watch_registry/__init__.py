"""Unified watch registry for /agent sessions and autonomous agents."""

from trade_integrations.watch_registry.scope import (
    combined_watch_spec_for_owner,
    nautilus_owner_id,
    parse_nautilus_owner_id,
    resolve_watch_scope_from_agent,
    symbols_for_owner,
    symbols_for_watch,
    union_symbols_for_owners,
)
from trade_integrations.watch_registry.store import (
    WatchMutationResult,
    create_watch,
    delete_watch,
    delete_watches_for_owner,
    get_watch,
    list_active_nautilus_owners,
    list_watches,
    list_watches_for_nautilus_owner,
    migrate_agent_watch_spec_to_registry,
    record_watch_fired,
    sync_nautilus_registry_from_watches,
    update_watch,
)

__all__ = [
    "combined_watch_spec_for_owner",
    "create_watch",
    "delete_watch",
    "delete_watches_for_owner",
    "get_watch",
    "list_active_nautilus_owners",
    "list_watches",
    "list_watches_for_nautilus_owner",
    "migrate_agent_watch_spec_to_registry",
    "nautilus_owner_id",
    "parse_nautilus_owner_id",
    "record_watch_fired",
    "resolve_watch_scope_from_agent",
    "symbols_for_owner",
    "symbols_for_watch",
    "sync_nautilus_registry_from_watches",
    "union_symbols_for_owners",
    "update_watch",
    "WatchMutationResult",
]
