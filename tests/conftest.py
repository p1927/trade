"""Trade-stack integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import trade_integrations  # noqa: F401 — apply runtime patches


@pytest.fixture(autouse=True)
def _isolate_trade_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep all hub persistence off reports/hub during tests.

    Many tests monkeypatch ``trade_integrations.context.hub.get_hub_dir``, but
    ``store`` imports that function directly — patching the hub module alone
    does not redirect writes. ``TRADE_STACK_HUB_DIR`` is read on every call,
    so setting it here isolates all autonomous agent / proposal I/O.
    """
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))


def patch_hub_wiki_dirs(monkeypatch: pytest.MonkeyPatch, hub: Path) -> None:
    """Redirect all hub wiki persistence to an isolated directory."""

    def _hub_dir() -> Path:
        return hub

    for target in (
        "trade_integrations.context.hub.get_hub_dir",
        "trade_integrations.hub_storage.news_events_store.get_hub_dir",
        "trade_integrations.hub_storage.news_event_index.get_hub_dir",
        "trade_integrations.hub_storage.verified_news_store.get_hub_dir",
        "trade_integrations.dataflows.hub_wiki.config.get_hub_dir",
        "trade_integrations.dataflows.hub_wiki.research.get_hub_dir",
    ):
        monkeypatch.setattr(target, _hub_dir)
