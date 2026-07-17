"""Tests for Skyvern bridge helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from trade_integrations.nse_browser.skyvern_bridge import (
    rows_from_skyvern_output,
    run_skyvern_task,
    skyvern_configured,
    skyvern_status,
)


def test_skyvern_not_configured_without_key(monkeypatch):
    monkeypatch.setenv("SKYVERN_ENABLED", "1")
    monkeypatch.delenv("SKYVERN_API_KEY", raising=False)
    with patch("trade_integrations.nse_browser.skyvern_bridge.read_local_skyvern_api_key", return_value=""):
        assert skyvern_configured() is False
        result = run_skyvern_task("test goal", url="https://example.com", persist=False)
    assert result["status"] == "error"
    assert result["error"] == "skyvern_not_configured"


def test_read_local_skyvern_api_key_from_credentials(tmp_path, monkeypatch):
    cred_dir = tmp_path / ".skyvern-data" / ".skyvern"
    cred_dir.mkdir(parents=True)
    cred_dir.joinpath("credentials.toml").write_text(
        '[skyvern]\nconfigs = [{"orgs" = [{cred="eyJ-test-token"}]}]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))
    from trade_integrations.nse_browser.skyvern_local import read_local_skyvern_api_key

    assert read_local_skyvern_api_key() == "eyJ-test-token"


def test_rows_from_skyvern_output_table_rows():
    out = {"table_rows": [{"date": "2026-01-01", "fii_net": 100}]}
    rows = rows_from_skyvern_output(out)
    assert len(rows) == 1
    assert rows[0]["fii_net"] == 100


def test_rows_from_skyvern_output_list():
    rows = rows_from_skyvern_output([{"date": "2026-01-02"}])
    assert len(rows) == 1


@patch("trade_integrations.nse_browser.skyvern_bridge.requests.get")
@patch("trade_integrations.nse_browser.skyvern_bridge.requests.post")
def test_run_skyvern_task_poll_completed(mock_post, mock_get, monkeypatch, tmp_path):
    monkeypatch.setenv("SKYVERN_ENABLED", "1")
    monkeypatch.setenv("SKYVERN_API_KEY", "test-key")
    monkeypatch.setenv("SKYVERN_TASK_TIMEOUT_S", "10")
    monkeypatch.setenv("SKYVERN_POLL_INTERVAL_S", "0.01")

    create_resp = MagicMock()
    create_resp.status_code = 200
    create_resp.json.return_value = {"run_id": "run_abc"}
    mock_post.return_value = create_resp

    poll_resp = MagicMock()
    poll_resp.status_code = 200
    poll_resp.json.return_value = {
        "status": "completed",
        "output": {"table_rows": [{"date": "2026-01-03", "net": 1}]},
    }
    mock_get.return_value = poll_resp

    with patch("trade_integrations.nse_browser.skyvern_bridge.hub_root", return_value=tmp_path):
        result = run_skyvern_task(
            "Extract table",
            url="https://example.com",
            output_schema={"type": "object"},
            persist=True,
            task_id="test_task",
        )

    assert result["status"] == "ok"
    assert result["run_id"] == "run_abc"
    assert result["structured_output"]["table_rows"][0]["net"] == 1
    artifact = tmp_path / "tasks" / "test_task" / "result.json"
    assert artifact.is_file()
    saved = json.loads(artifact.read_text())
    assert saved["engine"] == "skyvern"


@patch("trade_integrations.nse_browser.skyvern_bridge.requests.get")
def test_skyvern_status_unreachable(mock_get, monkeypatch):
    monkeypatch.setenv("SKYVERN_ENABLED", "1")
    monkeypatch.setenv("SKYVERN_API_KEY", "k")
    mock_get.side_effect = ConnectionError("refused")
    status = skyvern_status()
    assert status["reachable"] is False
    assert "refused" in str(status["error"])
