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
