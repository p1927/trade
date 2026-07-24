"""Tests for vision-guided navigation planner and executor (Phase 2)."""

from __future__ import annotations

import base64
import io

import pytest

from trade_integrations.dataflows.crawl4ai_client import vision_nav_enabled
from trade_integrations.dataflows.index_research.external_predictions.models import (
    NavigationStep,
)
from trade_integrations.dataflows.index_research.external_predictions.playwright_actions import (
    execute_vision_actions,
)
from trade_integrations.dataflows.index_research.external_predictions.vision_navigator import (
    plan_vision_navigation,
    validate_vision_actions,
)


def _tiny_jpeg_b64() -> str:
    from PIL import Image

    img = Image.new("RGB", (64, 64), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_validate_vision_actions_clamps_and_filters_enable() -> None:
    raw = [
        {"action": "click_text", "target": "Enable", "reason": "bad"},
        {"action": "click_text", "target": "Maybe Later", "reason": "ok"},
        {"action": "goto", "target": "https://evil.com", "reason": "bad"},
        {"action": "click_selector", "target": "#onetrust-accept-btn-handler", "reason": "ok"},
        {"action": "scroll", "target": "down:800", "reason": "ok"},
        {"action": "wait", "target": "500", "reason": "ok"},
        {"action": "press_key", "target": "Escape", "reason": "ok"},
        {"action": "done", "target": "", "reason": "ok"},
        {"action": "click_text", "target": "Accept All", "reason": "extra"},
    ]
    actions = validate_vision_actions(raw)
    assert len(actions) == 5
    targets = [a["target"] for a in actions]
    assert "Enable" not in targets
    assert "Maybe Later" in targets
    assert actions[-1]["action"] == "press_key"


def test_navigation_step_backward_compat_roundtrip() -> None:
    legacy = NavigationStep.from_dict(
        {"action": "click", "url": "https://example.com", "selector": "#x", "wait_ms": 250}
    )
    payload = legacy.to_dict()
    assert "target" not in payload
    assert payload["action"] == "click"
    restored = NavigationStep.from_dict(payload)
    assert restored.action == "click"
    assert restored.url == "https://example.com"

    extended = NavigationStep.from_dict(
        {"action": "click_text", "target": "Accept All", "wait_ms": 100}
    )
    ext_payload = extended.to_dict()
    assert ext_payload["target"] == "Accept All"
    assert NavigationStep.from_dict(ext_payload).target == "Accept All"


def test_navigation_step_unknown_action_defaults_to_goto() -> None:
    step = NavigationStep.from_dict({"action": "fly_to_moon", "url": "https://example.com"})
    assert step.action == "goto"


def test_vision_nav_enabled_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTERNAL_PREDICTIONS_EXPERT_VISION", "1")
    monkeypatch.delenv("EXTERNAL_PREDICTIONS_VISION_NAV", raising=False)
    assert vision_nav_enabled() is True

    monkeypatch.setenv("EXTERNAL_PREDICTIONS_VISION_NAV", "0")
    assert vision_nav_enabled() is False

    monkeypatch.setenv("EXTERNAL_PREDICTIONS_EXPERT_VISION", "0")
    monkeypatch.delenv("EXTERNAL_PREDICTIONS_VISION_NAV", raising=False)
    assert vision_nav_enabled() is False


def test_plan_vision_navigation_mocked_minimax(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import (
        vision_navigator as vn,
    )

    monkeypatch.setattr(vn, "minimax_configured", lambda: True)

    def _fake_call(**kwargs: object) -> dict[str, object]:
        return {
            "actions": [
                {"action": "click_text", "target": "Accept All", "reason": "cookie"},
                {"action": "done", "target": "", "reason": "clear"},
            ],
            "page_clear": True,
        }

    monkeypatch.setattr(vn, "call_minimax_vision_json", _fake_call)
    actions = plan_vision_navigation(
        screenshot_b64=_tiny_jpeg_b64(),
        url="https://economictimes.indiatimes.com/markets/stocks/news",
        goal="dismiss_overlays",
        block_reasons=["cookie_banner"],
    )
    assert len(actions) == 2
    assert actions[0]["action"] == "click_text"
    assert actions[1]["action"] == "done"


def test_plan_vision_navigation_raises_when_minimax_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import (
        vision_navigator as vn,
    )

    monkeypatch.setattr(vn, "minimax_configured", lambda: False)
    with pytest.raises(RuntimeError, match="MINIMAX_API_KEY"):
        plan_vision_navigation(
            screenshot_b64=_tiny_jpeg_b64(),
            url="https://example.com",
            goal="dismiss_overlays",
            block_reasons=["cookie_banner"],
        )


def test_plan_vision_browse_next_url_returns_pick(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import (
        vision_navigator as vn,
    )

    monkeypatch.setattr(vn, "minimax_configured", lambda: True)

    def _fake_call(**kwargs: object) -> dict[str, object]:
        return {"pick": 2, "reason": "forecast headline visible"}

    monkeypatch.setattr(vn, "call_minimax_vision_json", _fake_call)
    candidates = [
        ("Other story", "https://example.com/other"),
        ("Nifty 50 target", "https://example.com/nifty-forecast"),
    ]
    plan = vn.plan_vision_browse_next_url(
        screenshot_b64=_tiny_jpeg_b64(),
        url="https://example.com/markets",
        goal="pick_listing_link",
        candidates=candidates,
    )
    assert plan.next_url == "https://example.com/nifty-forecast"
    assert plan.pick_index == 2


def test_plan_vision_browse_next_url_accepts_direct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import (
        vision_navigator as vn,
    )

    monkeypatch.setattr(vn, "minimax_configured", lambda: True)
    monkeypatch.setattr(
        vn,
        "call_minimax_vision_json",
        lambda **kwargs: {
            "next_url": "https://example.com/markets/nifty/articleshow/1.cms",
            "reason": "article tile",
        },
    )
    plan = vn.plan_vision_browse_next_url(
        screenshot_b64=_tiny_jpeg_b64(),
        url="https://example.com/markets",
        goal="open_forecast_article",
    )
    assert plan.next_url.endswith("articleshow/1.cms")


class _MockLocator:
    def __init__(self, page: "_MockPage", kind: str, value: str) -> None:
        self._page = page
        self._kind = kind
        self._value = value
        self._fail = value == "__MISSING__"

    @property
    def first(self) -> "_MockLocator":
        return self

    async def click(self, timeout: int = 3000) -> None:
        if self._fail:
            raise TimeoutError("element not found")
        self._page.clicks.append((self._kind, self._value, timeout))


class _MockPage:
    def __init__(self) -> None:
        self.clicks: list[tuple[str, str, int]] = []
        self.eval_args: list[tuple[str, object]] = []
        self.scrolls: list[int] = []
        self.key_presses: list[str] = []
        self._js_clicks: dict[str, bool] = {"Maybe Later": True}

    def get_by_text(self, text: str, exact: bool = False) -> _MockLocator:
        return _MockLocator(self, "text", text)

    def locator(self, selector: str) -> _MockLocator:
        return _MockLocator(self, "selector", selector)

    async def evaluate(self, script: str, arg: object | None = None) -> object:
        self.eval_args.append((script, arg))
        if arg in self._js_clicks:
            return self._js_clicks[arg]
        return False

    @property
    def keyboard(self) -> "_MockKeyboard":
        return _MockKeyboard(self)


class _MockKeyboard:
    def __init__(self, page: _MockPage) -> None:
        self._page = page

    async def press(self, key: str) -> None:
        self._page.key_presses.append(key)


@pytest.mark.asyncio
async def test_execute_vision_actions_mock_page() -> None:
    page = _MockPage()
    page._js_clicks["Maybe Later"] = True
    actions = [
        {"action": "click_text", "target": "Maybe Later", "reason": "direct"},
        {"action": "click_selector", "target": "#accept", "reason": "selector"},
        {"action": "press_key", "target": "Escape", "reason": "close"},
        {"action": "scroll", "target": "down:400", "reason": "reveal"},
        {"action": "wait", "target": "10", "reason": "settle"},
        {"action": "done", "target": "", "reason": "finished"},
    ]
    result = await execute_vision_actions(page, actions)
    assert len(result["errors"]) == 0
    assert len(result["executed"]) == 6
    assert ("text", "Maybe Later", 3000) in page.clicks
    assert ("selector", "#accept", 3000) in page.clicks
    assert page.key_presses == ["Escape"]
    assert any(arg == 400 for _, arg in page.eval_args)


@pytest.mark.asyncio
async def test_execute_vision_actions_js_text_fallback() -> None:
    page = _MockPage()
    page._js_clicks["Maybe Later"] = True
    original_click = _MockLocator.click

    async def _fail_click(self: _MockLocator, timeout: int = 3000) -> None:
        raise TimeoutError("element not found")

    _MockLocator.click = _fail_click  # type: ignore[method-assign]
    try:
        result = await execute_vision_actions(
            page,
            [{"action": "click_text", "target": "Maybe Later", "reason": "js fallback"}],
        )
    finally:
        _MockLocator.click = original_click

    assert result["errors"] == []
    assert len(result["executed"]) == 1
    assert page.eval_args


@pytest.mark.asyncio
async def test_execute_vision_actions_collects_errors_without_aborting() -> None:
    page = _MockPage()
    result = await execute_vision_actions(
        page,
        [{"action": "scroll", "target": "sideways:1", "reason": "bad"}],
    )
    assert result["executed"] == []
    assert len(result["errors"]) == 1
    assert "Invalid scroll target" in result["errors"][0]["error"]
