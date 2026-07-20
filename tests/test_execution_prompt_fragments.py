"""Tests for autonomous prompt fragments (scheduler alignment)."""

from __future__ import annotations

import os

from trade_integrations.execution import prompt_fragments as pf


def test_research_turn_ack_only_when_schedule_disabled(monkeypatch):
    monkeypatch.delenv("AUTONOMOUS_RESEARCH_ON_SCHEDULE", raising=False)
    text = pf.prompt_fragment_for(
        "in_options_paper",
        agent_id="aa_test",
        focus="NIFTY",
        threshold=75,
        turn_kind="research",
    )
    assert "one-line ack" in text.lower()
    assert "get_options_trade_widget" not in text


def test_research_turn_full_flow_when_schedule_enabled(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_RESEARCH_ON_SCHEDULE", "1")
    text = pf.prompt_fragment_for(
        "in_options_paper",
        agent_id="aa_test",
        focus="NIFTY",
        threshold=75,
        turn_kind="research",
    )
    assert "scheduled research" in text.lower()
    assert "get_options_trade_widget" in text
    assert "one-line ack" not in text.lower()


def test_kind_note_research_matches_schedule_gate(monkeypatch):
    monkeypatch.delenv("AUTONOMOUS_RESEARCH_ON_SCHEDULE", raising=False)
    assert "skipped" in pf.kind_note_for("in_options_paper", "research").lower()

    monkeypatch.setenv("AUTONOMOUS_RESEARCH_ON_SCHEDULE", "true")
    note = pf.kind_note_for("in_options_paper", "research")
    assert "scheduled" in note.lower()
    assert "skipped" not in note.lower()
