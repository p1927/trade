"""Tests for Nautilus watch process management."""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_integrations.autonomous_agents import nautilus_watch as nw


@pytest.mark.unit
def test_reconcile_stale_watch_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    pidfile = log_dir / "nautilus-watch.pid"
    agent_file = log_dir / "nautilus-watch.agent_id"
    pidfile.write_text("999999", encoding="utf-8")
    agent_file.write_text("aa_dead", encoding="utf-8")

    monkeypatch.setattr(nw, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(nw, "_pidfile", lambda: pidfile)
    monkeypatch.setattr(nw, "_agent_id_file", lambda: agent_file)

    assert nw.reconcile_stale_watch_pid() is True
    assert not pidfile.exists()
    assert not agent_file.exists()


@pytest.mark.unit
def test_get_watch_process_status_clears_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    pidfile = log_dir / "nautilus-watch.pid"
    pidfile.write_text("999999", encoding="utf-8")

    monkeypatch.setattr(nw, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(nw, "_pidfile", lambda: pidfile)
    monkeypatch.setattr(nw, "_agent_id_file", lambda: log_dir / "nautilus-watch.agent_id")
    monkeypatch.setattr(nw, "_logfile", lambda: log_dir / "nautilus-watch.log")
    monkeypatch.setattr(nw, "_watch_enabled", lambda: True)

    status = nw.get_watch_process_status()
    assert status["alive"] is False
    assert status["pid"] is None


@pytest.mark.unit
def test_ensure_nautilus_watch_skips_launch_when_node_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    pidfile = log_dir / "nautilus-watch.pid"
    pidfile.write_text("424242", encoding="utf-8")
    registry = {
        "node_pid": 424242,
        "agents": [{"agent_id": "aa_live", "market": "IN", "symbols": ["NIFTY"]}],
    }
    reg_file = log_dir / "nautilus-watch.agents.json"
    reg_file.write_text(__import__("json").dumps(registry), encoding="utf-8")

    monkeypatch.setattr(nw, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(nw, "_pidfile", lambda: pidfile)
    monkeypatch.setattr(nw, "_registry_file", lambda: reg_file)
    monkeypatch.setattr(nw, "_watch_enabled", lambda: True)
    monkeypatch.setattr(nw, "_process_alive", lambda pid: pid == 424242)
    monkeypatch.setattr(nw, "is_agent_in_registry", lambda aid: aid == "aa_live")

    launched: list[bool] = []
    monkeypatch.setattr(nw, "_launch_watch", lambda **kw: launched.append(True))
    monkeypatch.setattr(
        nw,
        "add_agent_to_registry",
        lambda agent_id: registry,
    )

    assert nw.ensure_nautilus_watch_for_agent("aa_live") is None
    assert launched == []


@pytest.mark.unit
def test_ensure_nautilus_watch_stamps_handoff_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    pidfile = log_dir / "nautilus-watch.pid"
    pidfile.write_text("424242", encoding="utf-8")
    registry = {
        "node_pid": 424242,
        "agents": [{"agent_id": "aa_live", "market": "IN", "symbols": ["NIFTY"]}],
    }
    reg_file = log_dir / "nautilus-watch.agents.json"
    reg_file.write_text(__import__("json").dumps(registry), encoding="utf-8")

    monkeypatch.setattr(nw, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(nw, "_pidfile", lambda: pidfile)
    monkeypatch.setattr(nw, "_registry_file", lambda: reg_file)
    monkeypatch.setattr(nw, "_watch_enabled", lambda: True)
    monkeypatch.setattr(nw, "_process_alive", lambda pid: pid == 424242)
    monkeypatch.setattr(nw, "is_agent_in_registry", lambda aid: aid == "aa_live")
    monkeypatch.setattr(nw, "_launch_watch", lambda **kw: None)

    stamped: list[tuple[str, str]] = []

    def _fake_stamp(agent_id: str) -> None:
        stamped.append((agent_id, "2026-07-23T09:15:00+05:30"))

    monkeypatch.setattr(nw, "_stamp_handoff_market_context", _fake_stamp)

    assert nw.ensure_nautilus_watch_for_agent("aa_live") is None
    assert stamped == [("aa_live", "2026-07-23T09:15:00+05:30")]


@pytest.mark.unit
def test_get_research_status_marks_stages_complete_when_overall_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.research.orchestrator import ResearchResult, get_research_status
    from trade_integrations.research.registry import ResearchKind

    monkeypatch.setattr(
        "trade_integrations.research.orchestrator.ensure_research_complete",
        lambda *a, **k: ResearchResult(
            status="complete",
            kind=ResearchKind.OPTIONS,
            ticker="NIFTY",
            stages_run=["options_research:cache", "agent_debate"],
            debate_pending=False,
        ),
    )

    out = get_research_status("NIFTY", kind="options")
    assert out["status"] == "complete"
    assert all(s["complete"] for s in out["stages"])
