"""Third-party NIFTY forecast aggregation for the Prediction Miscellaneous tab."""

from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSnapshot,
    ExternalPredictionSource,
    ExternalPredictionTarget,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    add_source_to_watchlist,
    load_registry,
    remove_source_from_watchlist,
    save_registry,
    seed_registry_if_missing,
)
from trade_integrations.dataflows.index_research.external_predictions.store import (
    load_snapshot,
    upsert_prediction,
)

__all__ = [
    "ExternalPredictionRecord",
    "ExternalPredictionSnapshot",
    "ExternalPredictionSource",
    "ExternalPredictionTarget",
    "add_source_to_watchlist",
    "load_registry",
    "load_snapshot",
    "remove_source_from_watchlist",
    "save_registry",
    "seed_registry_if_missing",
    "upsert_prediction",
]
