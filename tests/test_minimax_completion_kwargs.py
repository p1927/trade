"""MiniMax OpenAI-compat kwargs normalization."""

from __future__ import annotations


def test_normalize_maps_max_tokens_to_max_completion_tokens():
    from trade_integrations.nse_browser.minimax_agent import normalize_completion_kwargs

    out = normalize_completion_kwargs(
        {"model": "MiniMax-M3", "max_tokens": 8192, "messages": []},
    )
    assert "max_tokens" not in out
    assert out["max_completion_tokens"] == 8192


def test_normalize_prefers_existing_max_completion_tokens():
    from trade_integrations.nse_browser.minimax_agent import normalize_completion_kwargs

    out = normalize_completion_kwargs(
        {
            "model": "MiniMax-M3",
            "max_tokens": 100,
            "max_completion_tokens": 2048,
            "messages": [],
        },
    )
    assert out["max_completion_tokens"] == 2048
    assert "max_tokens" not in out


def test_normalize_default_for_minimax_when_omitted(monkeypatch):
    from trade_integrations.nse_browser import minimax_agent as agent

    monkeypatch.setenv("MINIMAX_DEFAULT_COMPLETION_TOKENS", "4096")
    out = agent.normalize_completion_kwargs({"model": "MiniMax-M3", "messages": []})
    assert out["max_completion_tokens"] == 4096


def test_normalize_skips_non_minimax_models():
    from trade_integrations.nse_browser.minimax_agent import normalize_completion_kwargs

    out = normalize_completion_kwargs({"model": "gpt-4o", "max_tokens": 500, "messages": []})
    assert out["max_tokens"] == 500
    assert "max_completion_tokens" not in out


def test_merge_minimax_extra_body_defaults():
    from trade_integrations.nse_browser.minimax_agent import merge_minimax_extra_body

    body = merge_minimax_extra_body()
    assert body["reasoning_split"] is True
    assert body["thinking"] == {"type": "adaptive"}


def test_merge_minimax_extra_body_respects_thinking_disabled(monkeypatch):
    from trade_integrations.nse_browser.minimax_agent import merge_minimax_extra_body

    monkeypatch.setenv("MINIMAX_THINKING_DISABLED", "1")
    body = merge_minimax_extra_body()
    assert body["thinking"] == {"type": "disabled"}


def test_apply_minimax_request_payload_maps_tokens_and_extra_body(monkeypatch):
    from trade_integrations.nse_browser.minimax_agent import apply_minimax_request_payload

    monkeypatch.setenv("MINIMAX_DEFAULT_COMPLETION_TOKENS", "4096")
    payload = apply_minimax_request_payload({"max_tokens": 8192, "messages": []})
    assert payload["max_completion_tokens"] == 8192
    assert "max_tokens" not in payload
    assert payload["extra_body"]["reasoning_split"] is True
    assert payload["extra_body"]["thinking"] == {"type": "adaptive"}


def test_chat_completions_create_injects_extra_body(monkeypatch):
    from trade_integrations.nse_browser import minimax_agent as agent

    captured: dict = {}

    def _fake_queued(client, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(
        "trade_integrations.nse_browser.minimax_queue.chat_completions_create",
        _fake_queued,
    )
    agent.chat_completions_create(model="MiniMax-M3", messages=[])
    assert captured["extra_body"]["reasoning_split"] is True
    assert captured["extra_body"]["thinking"] == {"type": "adaptive"}
    assert captured["max_completion_tokens"] == agent._default_completion_tokens()
