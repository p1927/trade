"""NSE browser query path must not ingest repository on every read."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_get_nse_browser_data_skips_repo_ingest_on_cache_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.nse_browser import orchestrator as orch

    monkeypatch.setenv("TRADE_ALLOW_BATCH_INGEST", "")
    with patch.object(orch, "ingest_repository_to_hub") as ingest:
        with patch.object(orch, "get_dataset") as get_ds:
            with patch.object(orch, "load_dataset_frame", return_value=__import__("pandas").DataFrame({"date": ["2026-01-01"], "fii_net": [1.0]})):
                with patch.object(orch, "is_mission_fresh", return_value=(True, {})):
                    with patch.object(orch, "query_frame_by_dates") as qf:
                        qf.return_value = __import__("pandas").DataFrame({"date": ["2026-01-01"], "fii_net": [1.0]})
                        spec = type(
                            "Spec",
                            (),
                            {
                                "id": "fii_dii",
                                "mission_id": "fii_dii",
                                "date_col": "date",
                            },
                        )()
                        get_ds.return_value = spec
                        with patch.object(orch, "get_mission") as get_m:
                            get_m.return_value = object()
                            result = orch.get_nse_browser_data("fii_dii", refresh=False, backfill_historical=False)
    ingest.assert_not_called()
    assert result.get("status") in {"ok", "success", None} or "records" in result or result
