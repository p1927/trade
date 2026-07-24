"""Bounded parallel Crawl4AI fetch — sole entry point for browser crawls."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

logger = logging.getLogger(__name__)

_DATA_DIR = Path("_data") / "crawl4ai"
_LAST_BATCH = "last_batch.json"
_WAITING = "waiting.json"
_IN_FLIGHT = "in_flight.json"
_DEFAULT_MAX_PARALLEL = 2
_DEFAULT_INTER_REQUEST_DELAY_SEC = 0.0
_DEFAULT_DELAY_BEFORE_RETURN_HTML = 3.5


@dataclass
class CrawlPageResult:
    url: str
    success: bool
    markdown: str = ""
    title: str = ""
    error_message: str = ""
    elapsed_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


def crawl4ai_is_installed() -> bool:
    try:
        import crawl4ai  # noqa: F401

        return True
    except ImportError:
        return False


def _crawl4ai_data_dir() -> Path:
    path = get_hub_dir() / _DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json_int(path: Path, key: str) -> int:
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(data.get(key) or 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def _write_json_int(path: Path, key: str, value: int) -> None:
    path.write_text(json.dumps({key: max(0, value)}), encoding="utf-8")


def _waiting_path() -> Path:
    return _crawl4ai_data_dir() / _WAITING


def _in_flight_path() -> Path:
    return _crawl4ai_data_dir() / _IN_FLIGHT


def _last_batch_path() -> Path:
    return _crawl4ai_data_dir() / _LAST_BATCH


def _max_parallel() -> int:
    raw = os.environ.get("CRAWL4AI_MAX_PARALLEL", str(_DEFAULT_MAX_PARALLEL)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_PARALLEL


def _adjust_counter(path: Path, key: str, delta: int) -> None:
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        current = _read_json_int(path, key)
        _write_json_int(path, key, current + delta)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write_last_batch(payload: dict[str, Any]) -> None:
    _last_batch_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def crawl4ai_queue_stats() -> dict[str, Any]:
    last_batch: dict[str, Any] = {}
    path = _last_batch_path()
    if path.is_file():
        try:
            last_batch = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            last_batch = {}
    return {
        "installed": crawl4ai_is_installed(),
        "max_parallel": _max_parallel(),
        "waiting": _read_json_int(_waiting_path(), "waiting"),
        "in_flight": _read_json_int(_in_flight_path(), "in_flight"),
        "last_batch": last_batch,
    }


def _inter_request_delay_sec() -> float:
    raw = os.environ.get("CRAWL4AI_INTER_REQUEST_DELAY_SEC", "").strip()
    if not raw:
        return _DEFAULT_INTER_REQUEST_DELAY_SEC
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_INTER_REQUEST_DELAY_SEC


def _delay_before_return_html() -> float:
    raw = os.environ.get("CRAWL4AI_DELAY_BEFORE_RETURN_HTML", "").strip()
    if not raw:
        return _DEFAULT_DELAY_BEFORE_RETURN_HTML
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_DELAY_BEFORE_RETURN_HTML


def _cdp_url() -> str:
    return os.environ.get("CRAWL4AI_CDP_URL", "").strip()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _cdp_profile_dir() -> Path:
    raw = os.environ.get("CRAWL4AI_CDP_PROFILE_DIR", "").strip()
    if raw:
        return Path(raw)
    return _repo_root() / "log" / "chrome-cdp-profile"


def _cdp_port() -> int:
    raw = os.environ.get("CRAWL4AI_CDP_PORT", "9222").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 9222


def cdp_is_ready() -> bool:
    cdp = _cdp_url()
    if not cdp:
        return False
    try:
        import urllib.request

        with urllib.request.urlopen(f"{cdp.rstrip('/')}/json/version", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_cdp_ready(*, pipeline: Any | None = None) -> bool:
    """Start local Chrome CDP when configured but not reachable."""
    if not _cdp_url():
        return False
    if cdp_is_ready():
        return True

    script = _repo_root() / "scripts" / "start_chrome_cdp.sh"
    if not script.is_file():
        logger.warning("CDP configured but %s missing", script)
        return False

    profile_dir = _cdp_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    port = _cdp_port()
    if pipeline:
        pipeline.info("crawl4ai", f"Starting Chrome CDP on port {port}")
    else:
        logger.info("Starting Chrome CDP on port %s", port)

    import subprocess

    env = os.environ.copy()
    env["CRAWL4AI_CDP_PROFILE_DIR"] = str(profile_dir)
    try:
        subprocess.Popen(
            ["bash", str(script), str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except OSError as exc:
        logger.warning("Could not start Chrome CDP: %s", exc)
        return False

    deadline = time.time() + 20.0
    while time.time() < deadline:
        if cdp_is_ready():
            return True
        time.sleep(0.5)
    return False


def _proxy_url_raw() -> str:
    return (os.environ.get("CRAWL4AI_PROXY") or os.environ.get("HTTP_PROXY") or "").strip()


def _undetected_enabled() -> bool:
    return os.environ.get("CRAWL4AI_UNDETECTED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _parse_proxy_config() -> Any | None:
    raw = _proxy_url_raw()
    if not raw:
        return None
    from crawl4ai import ProxyConfig
    from urllib.parse import urlparse

    parsed = urlparse(raw)
    if not parsed.hostname:
        return ProxyConfig(server=raw)
    server = f"{parsed.scheme or 'http'}://{parsed.hostname}"
    if parsed.port:
        server = f"{server}:{parsed.port}"
    username = parsed.username or None
    password = parsed.password or None
    return ProxyConfig(server=server, username=username, password=password)


def browser_profile_tiers() -> list[str]:
    """Ordered browser profiles to try — CDP, proxy, undetected (optional), then stealth."""
    tiers: list[str] = []
    if _cdp_url():
        tiers.append("cdp")
    if _proxy_url_raw():
        tiers.append("proxy")
    if _undetected_enabled():
        tiers.append("undetected")
    tiers.append("stealth")
    seen: set[str] = set()
    out: list[str] = []
    for tier in tiers:
        if tier in seen:
            continue
        seen.add(tier)
        out.append(tier)
    return out


def primary_browser_profile() -> str:
    tiers = browser_profile_tiers()
    return tiers[0] if tiers else "stealth"


def next_browser_profile(current: str) -> str | None:
    tiers = browser_profile_tiers()
    try:
        idx = tiers.index(current)
    except ValueError:
        return None
    if idx + 1 >= len(tiers):
        return None
    return tiers[idx + 1]


def _browser_config(profile: str = "stealth") -> Any:
    from crawl4ai import BrowserConfig

    proxy_config = _parse_proxy_config()
    if profile == "cdp":
        cdp = _cdp_url()
        if cdp:
            return BrowserConfig(
                browser_mode="cdp",
                cdp_url=cdp,
                headless=False,
                enable_stealth=True,
                light_mode=True,
                text_mode=False,
            )
    if profile == "proxy" and proxy_config is not None:
        return BrowserConfig(
            headless=True,
            enable_stealth=True,
            light_mode=True,
            text_mode=False,
            proxy_config=proxy_config,
        )
    if profile == "undetected":
        return BrowserConfig(
            headless=True,
            enable_stealth=True,
            light_mode=False,
            text_mode=False,
        )
    return BrowserConfig(
        headless=True,
        enable_stealth=True,
        light_mode=True,
        text_mode=False,
        proxy_config=proxy_config if profile == "stealth" and proxy_config else None,
    )


def _make_crawler(profile: str) -> Any:
    from crawl4ai import AsyncWebCrawler, UndetectedAdapter
    from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy

    config = _browser_config(profile)
    if profile == "undetected":
        strategy = AsyncPlaywrightCrawlerStrategy(
            browser_config=config,
            browser_adapter=UndetectedAdapter(),
        )
        return AsyncWebCrawler(crawler_strategy=strategy, config=config)
    return AsyncWebCrawler(config=config)


def _scroll_before_screenshot() -> bool:
    raw = os.environ.get("EXTERNAL_PREDICTIONS_SCROLL_BEFORE_SCREENSHOT", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_enabled(name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _remove_consent_popups() -> bool:
    return _env_enabled("CRAWL4AI_REMOVE_CONSENT_POPUPS", default=True)


def _remove_overlay_elements() -> bool:
    return _env_enabled("CRAWL4AI_REMOVE_OVERLAY_ELEMENTS", default=True)


# Shared helpers + multi-pass dismiss (runs in Crawl4AI js_code slot, after scan_full_page scroll).
_POPUP_SCROLL_RESET_JS = """
  window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
  for (const selector of ["main", "#content", ".pageContent", ".main_container"]) {
    const el = document.querySelector(selector);
    if (el) {
      el.scrollTop = 0;
    }
  }
""".strip()
_POPUP_DISMISS_JS_HELPERS = """
  const visible = (el) => {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    return s.display !== "none" && s.visibility !== "hidden" && parseFloat(s.opacity || "1") > 0;
  };
  const clickVisible = (selector) => {
    let clicked = false;
    for (const el of document.querySelectorAll(selector)) {
      if (visible(el)) {
        el.click();
        clicked = true;
      }
    }
    return clicked;
  };
  const removeMatching = (selector) => {
    document.querySelectorAll(selector).forEach((el) => el.remove());
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
""".strip()

_CONSENT_DISMISS_JS_BODY = """
    if (typeof window.OneTrust !== "undefined") {
      try {
        if (typeof window.OneTrust.AllowAll === "function") window.OneTrust.AllowAll();
        if (typeof window.OneTrust.RejectAll === "function") { /* prefer AllowAll */ }
      } catch (e) {}
    }
    const consentSelectors = [
      "#onetrust-accept-btn-handler",
      "#accept-recommended-btn-handler",
      "#onetrust-pc-btn-handler",
      "#truste-consent-button",
      ".fc-cta-consent",
      ".fc-button.fc-cta-consent.fc-primary-button",
      ".accept_recommended_btn",
      ".privacy_policy_button",
      '[data-testid="accept-all"]',
      'button[id*="accept" i]',
      'button[class*="accept" i]',
      'button[class*="agree" i]',
      'button[class*="close" i]',
      'span[class*="close" i]',
      'a[class*="close" i]',
      '[aria-label*="close" i]',
      '[title*="close" i]',
      ".close_icon",
      ".close-btn",
      ".modal-close",
      ".popup-close",
      ".notNow",
      ".no-thanks",
      '[class*="dismiss" i]',
    ];
    for (const selector of consentSelectors) {
      clickVisible(selector);
    }
    for (const el of document.querySelectorAll("a, button, span, div")) {
      const text = (el.textContent || "").trim();
      if (/^maybe later$/i.test(text) && visible(el)) {
        el.click();
      }
    }
    const dismissPatterns = [
      /^accept\\s*(all)?(\\s*cookies)?$/i,
      /^allow\\s*(all)?(\\s*cookies)?$/i,
      /^i\\s*agree$/i,
      /^agree$/i,
      /^skip$/i,
      /^not now$/i,
      /^no thanks$/i,
      /^maybe later$/i,
      /^continue reading$/i,
      /^close$/i,
      /^×$/,
      /^✕$/,
    ];
    for (const el of document.querySelectorAll("button, a, span[role='button'], div[role='button']")) {
      const text = (el.textContent || "").trim();
      if (text.length > 0 && text.length <= 48 && dismissPatterns.some((p) => p.test(text)) && visible(el)) {
        el.click();
      }
    }
""".strip()

_OVERLAY_DISMISS_JS_BODY = """
    const overlaySelectors = [
      "#onetrust-consent-sdk",
      "#onetrust-banner-sdk",
      ".onetrust-pc-dark-filter",
      "#onetrust-pc-sdk",
      ".blk_overlay",
      ".popup_container",
      "#webpushBanner",
      ".modal_popup",
      ".popupBox",
      '[class*="webpush" i]',
      '[id*="webpush" i]',
      '[class*="notif" i][class*="popup" i]',
      '[class*="notification" i][class*="popup" i]',
      "#myModal",
      ".paywall-overlay",
      '[class*="cookie-consent" i]',
      '[class*="cookie-banner" i]',
      '[class*="consent-banner" i]',
      '[class*="app-download" i]',
      '[id*="app-download" i]',
      '[class*="subscription-modal" i]',
      '[class*="login-modal" i]',
      '[class*="newsletter" i][class*="popup" i]',
    ];
    for (const selector of overlaySelectors) removeMatching(selector);
    document.body.classList.remove("ot-overflow-hidden");
    document.documentElement.classList.remove("ot-overflow-hidden");
""".strip()

_POPUP_DISMISS_ONCE_BODY = """
  async function dismissOnce() {
    __CONSENT__
    __OVERLAY__
    document.body.style.overflow = "";
    document.body.style.overflowY = "";
    document.documentElement.style.overflow = "";
    document.documentElement.style.overflowY = "";
  }
""".strip()


def _popup_dismiss_once_body(*, consent: bool, overlay: bool) -> str:
    return _POPUP_DISMISS_ONCE_BODY.replace(
        "__CONSENT__",
        _CONSENT_DISMISS_JS_BODY if consent else "",
    ).replace(
        "__OVERLAY__",
        _OVERLAY_DISMISS_JS_BODY if overlay else "",
    )


def _popup_dismiss_js(*, consent: bool, overlay: bool) -> str | None:
    """Build dismiss JS for Crawl4AI js_code (post-wait). Must be raw async body — not an IIFE."""
    if not consent and not overlay:
        return None
    once_body = _popup_dismiss_once_body(consent=consent, overlay=overlay)
    return "\n  ".join(
        [
            _POPUP_DISMISS_JS_HELPERS,
            _POPUP_SCROLL_RESET_JS,
            once_body,
            """
  for (let pass = 0; pass < 3; pass += 1) {
    await dismissOnce();
    await sleep(350);
  }
  window.scrollTo(0, 0);
  await sleep(400);
  for (let pass = 0; pass < 2; pass += 1) {
    await dismissOnce();
    await sleep(350);
  }
  window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
  for (const selector of ["main", "#content", ".pageContent", ".main_container"]) {
    const el = document.querySelector(selector);
    if (el) {
      el.scrollTop = 0;
    }
  }
  await sleep(500);
  for (let late = 0; late < 6; late += 1) {
    await dismissOnce();
    const alertText = document.body?.innerText || "";
    if (!/maybe later|get top news alerts|we value your privacy|accept all/i.test(alertText)) {
      break;
    }
    await sleep(500);
  }
""".strip(),
        ]
    )


def _popup_dismiss_early_js(*, consent: bool, overlay: bool) -> str | None:
    """Early dismiss before wait_for — poll OneTrust/CMP as soon as the banner mounts."""
    if not consent and not overlay:
        return None
    once_body = _popup_dismiss_once_body(consent=consent, overlay=overlay)
    return "\n  ".join(
        [
            _POPUP_DISMISS_JS_HELPERS,
            _POPUP_SCROLL_RESET_JS,
            once_body,
            """
  for (let attempt = 0; attempt < 16; attempt += 1) {
    await dismissOnce();
    const banner = document.querySelector(
      "#onetrust-banner-sdk, #onetrust-consent-sdk, .fc-consent-root, .truste_overlay"
    );
    if (!banner || !visible(banner)) {
      break;
    }
    await sleep(500);
  }
""".strip(),
        ]
    )


def _run_config(*, score_links: bool = False, screenshot: bool = False) -> Any:
    from crawl4ai import CacheMode, CrawlerRunConfig

    remove_consent = _remove_consent_popups()
    remove_overlay = _remove_overlay_elements()
    kwargs: dict[str, Any] = {
        "cache_mode": CacheMode.BYPASS,
        "word_count_threshold": 5,
        "screenshot": screenshot,
        "delay_before_return_html": _delay_before_return_html(),
        "remove_consent_popups": remove_consent,
        # Crawl4AI built-in overlay removal is too aggressive on ET (removes main content).
        # Targeted overlay removal lives in js_code when CRAWL4AI_REMOVE_OVERLAY_ELEMENTS=1.
        "remove_overlay_elements": False,
        "locale": "en-IN",
        "timezone_id": "Asia/Kolkata",
    }
    try:
        from crawl4ai import GeolocationConfig

        kwargs["geolocation"] = GeolocationConfig(
            latitude=19.0760,
            longitude=72.8777,
            accuracy=100,
        )
    except ImportError:
        logger.debug("GeolocationConfig unavailable; crawl without India geo hints")
    dismiss_js = _popup_dismiss_js(consent=remove_consent, overlay=remove_overlay)
    early_dismiss_js = _popup_dismiss_early_js(consent=remove_consent, overlay=remove_overlay)
    if dismiss_js:
        kwargs["js_code"] = dismiss_js
    if early_dismiss_js:
        kwargs["js_code_before_wait"] = early_dismiss_js
    if screenshot:
        # Full-page stitch re-scrolls after dismiss and re-captures fixed cookie modals.
        kwargs["force_viewport_screenshot"] = True
        kwargs["screenshot_wait_for"] = 1.0
    popup_dismiss_active = remove_consent or remove_overlay
    if screenshot and _scroll_before_screenshot() and not popup_dismiss_active:
        # scan_full_page runs before js_code in Crawl4AI — skip when dismissing popups.
        kwargs["scan_full_page"] = True
        kwargs["delay_before_return_html"] = max(
            float(kwargs["delay_before_return_html"] or 0),
            1.5,
        )
    elif screenshot and popup_dismiss_active:
        kwargs["delay_before_return_html"] = max(
            float(kwargs["delay_before_return_html"] or 0),
            2.0,
        )
    if score_links:
        try:
            from crawl4ai import LinkPreviewConfig

            kwargs["score_links"] = True
            kwargs["link_preview_config"] = LinkPreviewConfig(
                query="Nifty 50 index target forecast outlook analyst",
                score_threshold=0.25,
            )
        except ImportError:
            logger.debug("LinkPreviewConfig unavailable; listing crawl without link scoring")
    return CrawlerRunConfig(**kwargs)


def _serialize_native_links(links_obj: Any) -> list[dict[str, Any]]:
    """Normalize Crawl4AI internal links for downstream discovery."""
    if links_obj is None:
        return []
    internal = getattr(links_obj, "internal", None)
    if internal is None and isinstance(links_obj, dict):
        internal = links_obj.get("internal")
    rows: list[dict[str, Any]] = []
    for link in internal or []:
        if isinstance(link, dict):
            href = str(link.get("href") or "").strip()
            text = str(link.get("text") or link.get("title") or "").strip()
            total_score = link.get("total_score")
        else:
            href = str(getattr(link, "href", "") or "").strip()
            text = str(getattr(link, "text", "") or getattr(link, "title", "") or "").strip()
            total_score = getattr(link, "total_score", None)
        if not href:
            continue
        rows.append(
            {
                "href": href,
                "text": text,
                "title": text,
                "total_score": total_score,
            }
        )
    return rows


def _screenshots_enabled() -> bool:
    return os.environ.get("EXTERNAL_PREDICTIONS_SCREENSHOTS", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _finalize_crawl_result(
    *,
    url: str,
    batch_profile: str,
    markdown: str,
    title: str,
    metadata: dict[str, Any],
    elapsed_ms: float,
) -> CrawlPageResult:
    from trade_integrations.dataflows.index_research.external_predictions.crawl_resilience import (
        is_akamai_wrapped_markdown,
    )

    if is_akamai_wrapped_markdown(markdown, url):
        return CrawlPageResult(
            url=url,
            success=False,
            markdown=markdown,
            title=title,
            error_message="Akamai wrapped response (geo edge)",
            elapsed_ms=elapsed_ms,
            metadata={**metadata, "akamai_wrapped": True, "browser_profile": batch_profile},
        )
    return CrawlPageResult(
        url=url,
        success=True,
        markdown=markdown,
        title=title,
        elapsed_ms=elapsed_ms,
        metadata=metadata,
    )


async def crawl_urls_parallel(
    urls: list[str],
    *,
    max_parallel: int | None = None,
    pipeline: Any | None = None,
    score_links: bool = False,
    capture_screenshot: bool | None = None,
    browser_profile: str | None = None,
    escalate_on_bot_block: bool = True,
) -> list[CrawlPageResult]:
    """Fetch URLs concurrently via one shared AsyncWebCrawler process."""
    from trade_integrations.dataflows.index_research.external_predictions.crawl_resilience import (
        is_blocklisted_crawl_domain,
        is_crawl_bot_blocked,
    )
    from trade_integrations.dataflows import source_availability

    cleaned = [u.strip() for u in urls if str(u or "").strip()]
    if not cleaned:
        return []

    if not crawl4ai_is_installed():
        msg = "crawl4ai not installed — run: pip install 'trade-stack[external-predictions]' && crawl4ai-setup"
        if pipeline:
            pipeline.error("crawl4ai", msg)
        return [CrawlPageResult(url=u, success=False, error_message=msg) for u in cleaned]

    if not source_availability.should_attempt("crawl4ai", "fetch"):
        msg = "Crawl4AI circuit open — browser fetch temporarily unavailable"
        if pipeline:
            pipeline.warn("crawl4ai", msg)
        return [CrawlPageResult(url=u, success=False, error_message=msg) for u in cleaned]

    parallel = max_parallel or _max_parallel()
    want_screenshot = _screenshots_enabled() if capture_screenshot is None else capture_screenshot
    profile = browser_profile or primary_browser_profile()
    if profile == "cdp" or any(is_blocklisted_crawl_domain(u) for u in cleaned):
        if _cdp_url() and not ensure_cdp_ready(pipeline=pipeline):
            msg = f"CDP endpoint at {_cdp_url()} is not ready after startup"
            if pipeline:
                pipeline.warn("crawl4ai", msg)
            return [CrawlPageResult(url=u, success=False, error_message=msg) for u in cleaned]
        if any(is_blocklisted_crawl_domain(u) for u in cleaned) and "cdp" in browser_profile_tiers():
            profile = "cdp"
    if pipeline:
        pipeline.info(
            "crawl4ai",
            f"Launching {profile} parallel crawl ({len(cleaned)} URL(s), max_parallel={parallel})",
        )

    _adjust_counter(_waiting_path(), "waiting", len(cleaned))
    batch_started = time.time()
    results_by_url: dict[str, CrawlPageResult] = {}
    results: list[CrawlPageResult] = []
    batch_error = ""
    inter_delay = _inter_request_delay_sec()

    async def _run_batch(url_list: list[str], batch_profile: str) -> None:
        async with _make_crawler(batch_profile) as crawler:
            semaphore = asyncio.Semaphore(parallel)

            async def _crawl_one(url: str) -> CrawlPageResult:
                started = time.time()
                _adjust_counter(_in_flight_path(), "in_flight", 1)
                _adjust_counter(_waiting_path(), "waiting", -1)
                if pipeline:
                    pipeline.info(
                        "crawl4ai",
                        f"Fetching ({batch_profile}) {url[:100]}",
                        url=url,
                    )
                try:
                    if inter_delay > 0 and is_blocklisted_crawl_domain(url):
                        await asyncio.sleep(inter_delay)
                    async with semaphore:
                        result = await crawler.arun(
                            url=url,
                            config=_run_config(score_links=score_links, screenshot=want_screenshot),
                        )
                    if inter_delay > 0:
                        await asyncio.sleep(inter_delay)
                    elapsed_ms = (time.time() - started) * 1000.0
                    if result.success:
                        markdown = str(getattr(result, "markdown", "") or "")
                        title = ""
                        metadata = dict(getattr(result, "metadata", None) or {})
                        metadata["browser_profile"] = batch_profile
                        native_links = _serialize_native_links(getattr(result, "links", None))
                        if native_links:
                            metadata["links"] = native_links
                        screenshot = getattr(result, "screenshot", None)
                        if screenshot:
                            metadata["screenshot_b64"] = str(screenshot)
                        if metadata:
                            title = str(metadata.get("title") or "")
                        if pipeline:
                            pipeline.info(
                                "crawl4ai",
                                f"OK ({len(markdown)} chars, {elapsed_ms:.0f}ms)",
                                url=url,
                            )
                        finalized = _finalize_crawl_result(
                            url=url,
                            batch_profile=batch_profile,
                            markdown=markdown,
                            title=title,
                            metadata=metadata,
                            elapsed_ms=elapsed_ms,
                        )
                        if finalized.success:
                            return await _maybe_vision_recover_crawl(
                                finalized,
                                url=url,
                                markdown=markdown,
                                title=title,
                                metadata=metadata,
                                want_screenshot=want_screenshot,
                                pipeline=pipeline,
                            )
                        return finalized
                    error_message = str(getattr(result, "error_message", "") or "Crawl failed")
                    if pipeline:
                        pipeline.warn("crawl4ai", error_message, url=url)
                    return CrawlPageResult(
                        url=url,
                        success=False,
                        error_message=error_message,
                        elapsed_ms=elapsed_ms,
                        metadata={"browser_profile": batch_profile},
                    )
                except Exception as exc:
                    elapsed_ms = (time.time() - started) * 1000.0
                    if pipeline:
                        pipeline.warn("crawl4ai", str(exc), url=url)
                    return CrawlPageResult(
                        url=url,
                        success=False,
                        error_message=str(exc),
                        elapsed_ms=elapsed_ms,
                        metadata={"browser_profile": batch_profile},
                    )
                finally:
                    _adjust_counter(_in_flight_path(), "in_flight", -1)

            gathered = await asyncio.gather(
                *[_crawl_one(url) for url in url_list],
                return_exceptions=True,
            )
            for url, item in zip(url_list, gathered):
                if isinstance(item, Exception):
                    results_by_url[url] = CrawlPageResult(
                        url=url,
                        success=False,
                        error_message=str(item),
                        metadata={"browser_profile": batch_profile},
                    )
                else:
                    results_by_url[url] = item

    try:
        await _run_batch(cleaned, profile)

        if escalate_on_bot_block:
            blocked_urls: list[str] = []
            for url in cleaned:
                row = results_by_url.get(url)
                if row is None:
                    continue
                if row.success and (row.markdown or "").strip():
                    continue
                if not is_crawl_bot_blocked(row, url):
                    continue
                if is_blocklisted_crawl_domain(url) and profile == "cdp":
                    # Akamai-heavy domains (e.g. moneycontrol.com) do not improve on stealth.
                    continue
                blocked_urls.append(url)
            next_profile = next_browser_profile(profile)
            if blocked_urls and next_profile:
                if pipeline:
                    pipeline.info(
                        "crawl4ai",
                        f"Bot block on {len(blocked_urls)} URL(s) — retry with {next_profile}",
                    )
                _adjust_counter(_waiting_path(), "waiting", len(blocked_urls))
                await _run_batch(blocked_urls, next_profile)
                for url in blocked_urls:
                    retry_row = results_by_url.get(url)
                    if retry_row is not None:
                        retry_row.metadata = {
                            **dict(retry_row.metadata or {}),
                            "bot_profile_tried": profile,
                        }

        results = [results_by_url[u] for u in cleaned if u in results_by_url]

        ok_count = sum(1 for row in results if row.success)
        if ok_count:
            source_availability.record_success("crawl4ai", "fetch")
        else:
            source_availability.record_failure("crawl4ai", "fetch", "all URLs failed")
    except Exception as exc:
        batch_error = str(exc)
        source_availability.record_failure("crawl4ai", "fetch", exc)
        if pipeline:
            pipeline.error("crawl4ai", batch_error)
        results = [
            CrawlPageResult(url=u, success=False, error_message=batch_error or str(exc))
            for u in cleaned
        ]
    finally:
        remaining = _read_json_int(_waiting_path(), "waiting")
        if remaining > 0:
            _adjust_counter(_waiting_path(), "waiting", -remaining)
        elapsed_ms = (time.time() - batch_started) * 1000.0
        _write_last_batch(
            {
                "url_count": len(cleaned),
                "success_count": sum(1 for row in results if row.success),
                "elapsed_ms": round(elapsed_ms, 1),
                "max_parallel": parallel,
                "browser_profile": profile,
                "error": batch_error,
                "finished_at": time.time(),
            }
        )
        if pipeline:
            pipeline.info(
                "crawl4ai",
                f"Batch complete — {sum(1 for row in results if row.success)}/{len(cleaned)} OK "
                f"in {elapsed_ms:.0f}ms",
            )

    return results


def vision_nav_enabled() -> bool:
    """Env gate for vision navigation recovery (default on when vision_enabled)."""
    from trade_integrations.dataflows.index_research.external_predictions.minimax_vision import (
        vision_enabled,
    )

    default = "1" if vision_enabled() else "0"
    return os.environ.get("EXTERNAL_PREDICTIONS_VISION_NAV", default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _vision_nav_max_rounds() -> int:
    raw = os.environ.get("EXTERNAL_PREDICTIONS_VISION_NAV_MAX_ROUNDS", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


async def _maybe_vision_recover_crawl(
    row: CrawlPageResult,
    *,
    url: str,
    markdown: str,
    title: str,
    metadata: dict[str, Any],
    want_screenshot: bool,
    pipeline: Any | None,
) -> CrawlPageResult:
    """Run vision navigation when tier-0 crawl still looks blocked."""
    if not want_screenshot or not vision_nav_enabled():
        return row
    from trade_integrations.dataflows.index_research.external_predictions.page_block_detector import (
        detect_blocked_page,
    )

    screenshot_b64 = str(metadata.get("screenshot_b64") or "")
    block_signal = detect_blocked_page(
        url=url,
        markdown=markdown,
        screenshot_b64=screenshot_b64 or None,
        title=title,
    )
    if not block_signal.blocked:
        return row
    if pipeline:
        pipeline.info(
            "vision_nav",
            f"Blocked ({', '.join(block_signal.reasons)}) — starting vision recovery",
            url=url,
        )
    return await vision_navigate_url(
        url,
        pipeline=pipeline,
        max_rounds=_vision_nav_max_rounds(),
    )


async def _page_markdown_and_screenshot(page: Any) -> tuple[str, str, str]:
    """Return (title, markdown-ish text, screenshot_b64) from a live Playwright page."""
    import base64

    title = str(await page.title() or "")
    markdown = str(
        await page.evaluate(
            "() => (document.body && document.body.innerText) ? document.body.innerText : ''"
        )
        or ""
    )
    shot = await page.screenshot(full_page=True, type="jpeg")
    screenshot_b64 = base64.b64encode(shot).decode("ascii")
    return title, markdown, screenshot_b64


async def _connect_cdp_playwright_page(url: str) -> tuple[Any, Any, Any] | None:
    """Connect to configured CDP and return (playwright, browser, page) or None."""
    cdp = _cdp_url()
    if not cdp:
        return None
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.debug("playwright not installed; vision navigation requires playwright")
        return None

    playwright = await async_playwright().start()
    browser = await playwright.chromium.connect_over_cdp(cdp)
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    return playwright, browser, page


async def vision_navigate_url(
    url: str,
    *,
    pipeline: Any | None = None,
    max_rounds: int = 3,
) -> CrawlPageResult:
    """Crawl URL with optional vision-guided overlay recovery (Phase 2 skeleton)."""
    from trade_integrations.dataflows.index_research.external_predictions.page_block_detector import (
        detect_blocked_page,
    )
    from trade_integrations.dataflows.index_research.external_predictions.playwright_actions import (
        execute_vision_actions,
    )
    from trade_integrations.dataflows.index_research.external_predictions.vision_navigator import (
        plan_vision_navigation,
        vision_nav_goal_from_block_reasons,
    )

    cleaned = str(url or "").strip()
    if not cleaned:
        return CrawlPageResult(url=url, success=False, error_message="empty URL")

    if not crawl4ai_is_installed():
        msg = "crawl4ai not installed — run: pip install 'trade-stack[external-predictions]' && crawl4ai-setup"
        return CrawlPageResult(url=cleaned, success=False, error_message=msg)

    profile = "cdp" if _cdp_url() else primary_browser_profile()
    if profile == "cdp" and not ensure_cdp_ready(pipeline=pipeline):
        msg = f"CDP endpoint at {_cdp_url()} is not ready after startup"
        if pipeline:
            pipeline.warn("crawl4ai", msg)
        return CrawlPageResult(url=cleaned, success=False, error_message=msg)

    started = time.time()
    want_screenshot = _screenshots_enabled()
    metadata: dict[str, Any] = {"browser_profile": profile, "vision_nav": False}

    async with _make_crawler(profile) as crawler:
        result = await crawler.arun(
            url=cleaned,
            config=_run_config(screenshot=want_screenshot),
        )

    elapsed_ms = (time.time() - started) * 1000.0
    if not result.success:
        error_message = str(getattr(result, "error_message", "") or "Crawl failed")
        return CrawlPageResult(
            url=cleaned,
            success=False,
            error_message=error_message,
            elapsed_ms=elapsed_ms,
            metadata=metadata,
        )

    markdown = str(getattr(result, "markdown", "") or "")
    title = ""
    result_metadata = dict(getattr(result, "metadata", None) or {})
    if result_metadata:
        title = str(result_metadata.get("title") or "")
    screenshot_b64 = str(getattr(result, "screenshot", "") or result_metadata.get("screenshot_b64") or "")

    block_signal = detect_blocked_page(
        url=cleaned,
        markdown=markdown,
        screenshot_b64=screenshot_b64 or None,
        title=title,
    )
    if not block_signal.blocked or not vision_nav_enabled() or not _cdp_url():
        metadata.update(result_metadata)
        metadata["vision_nav"] = False
        metadata["block_reasons"] = block_signal.reasons
        if screenshot_b64:
            metadata["screenshot_b64"] = screenshot_b64
        return _finalize_crawl_result(
            url=cleaned,
            batch_profile=profile,
            markdown=markdown,
            title=title,
            metadata=metadata,
            elapsed_ms=elapsed_ms,
        )

    if pipeline:
        pipeline.info(
            "crawl4ai",
            f"Blocked page ({', '.join(block_signal.reasons)}) — starting vision navigation",
            url=cleaned,
        )

    playwright_handle: Any | None = None
    browser_handle: Any | None = None
    page_handle: Any | None = None
    prior_actions: list[dict[str, str]] = []
    vision_rounds = 0
    vision_errors: list[dict[str, Any]] = []
    vision_nav_steps: list[dict[str, Any]] = []
    block_signal_after = block_signal

    try:
        connected = await _connect_cdp_playwright_page(cleaned)
        if connected is None:
            metadata.update(result_metadata)
            metadata["vision_nav"] = False
            metadata["vision_nav_skipped"] = "cdp_playwright_unavailable"
            metadata["block_reasons"] = block_signal.reasons
            if screenshot_b64:
                metadata["screenshot_b64"] = screenshot_b64
            return _finalize_crawl_result(
                url=cleaned,
                batch_profile=profile,
                markdown=markdown,
                title=title,
                metadata=metadata,
                elapsed_ms=elapsed_ms,
            )

        playwright_handle, _browser_handle, page_handle = connected
        goal = vision_nav_goal_from_block_reasons(block_signal.reasons)

        for _round in range(max(1, max_rounds)):
            title, markdown, screenshot_b64 = await _page_markdown_and_screenshot(page_handle)
            block_signal = detect_blocked_page(
                url=cleaned,
                markdown=markdown,
                screenshot_b64=screenshot_b64,
                title=title,
            )
            if not block_signal.blocked:
                break
            if not screenshot_b64:
                break
            try:
                actions = plan_vision_navigation(
                    screenshot_b64=screenshot_b64,
                    url=cleaned,
                    goal=goal,
                    block_reasons=block_signal.reasons,
                    prior_actions=prior_actions,  # type: ignore[arg-type]
                )
            except RuntimeError as exc:
                vision_errors.append({"round": _round, "error": str(exc)})
                break
            if not actions:
                break
            exec_result = await execute_vision_actions(page_handle, actions, pipeline=pipeline)
            vision_rounds += 1
            prior_actions.extend(actions)
            vision_nav_steps.extend(exec_result.get("executed") or [])
            vision_errors.extend(exec_result.get("errors") or [])
            if pipeline:
                pipeline.info(
                    "vision_nav",
                    f"Round {vision_rounds}: {len(actions)} planned action(s)",
                    url=cleaned,
                )
            await asyncio.sleep(0.5)

        title, markdown, screenshot_b64 = await _page_markdown_and_screenshot(page_handle)
        block_signal_after = detect_blocked_page(
            url=cleaned,
            markdown=markdown,
            screenshot_b64=screenshot_b64,
            title=title,
        )
    except Exception as exc:
        if pipeline:
            pipeline.warn("crawl4ai", f"Vision navigation failed: {exc}", url=cleaned)
        vision_errors.append({"error": str(exc)})
    finally:
        if playwright_handle is not None:
            try:
                await playwright_handle.stop()
            except Exception:
                logger.debug("playwright stop failed after vision navigation", exc_info=True)

    elapsed_ms = (time.time() - started) * 1000.0
    metadata.update(result_metadata)
    metadata["vision_nav"] = vision_rounds > 0
    metadata["vision_nav_rounds"] = vision_rounds
    metadata["block_reasons"] = block_signal_after.reasons
    if vision_nav_steps:
        metadata["vision_nav_steps"] = vision_nav_steps
    if vision_errors:
        metadata["vision_nav_errors"] = vision_errors
    if vision_rounds > 0 and pipeline:
        pipeline.info(
            "vision_nav",
            f"Recovery complete — {vision_rounds} round(s), "
            f"blocked_after={block_signal_after.blocked}",
            url=cleaned,
        )
    if screenshot_b64:
        metadata["screenshot_b64"] = screenshot_b64

    return _finalize_crawl_result(
        url=cleaned,
        batch_profile=profile,
        markdown=markdown,
        title=title,
        metadata=metadata,
        elapsed_ms=elapsed_ms,
    )


def crawl_urls_parallel_sync(
    urls: list[str],
    *,
    max_parallel: int | None = None,
    pipeline: Any | None = None,
    score_links: bool = False,
    capture_screenshot: bool | None = None,
) -> list[CrawlPageResult]:
    """Sync wrapper for refresh workers running outside an event loop."""
    return asyncio.run(
        crawl_urls_parallel(
            urls,
            max_parallel=max_parallel,
            pipeline=pipeline,
            score_links=score_links,
            capture_screenshot=capture_screenshot,
        )
    )


def vision_navigate_url_sync(
    url: str,
    *,
    pipeline: Any | None = None,
    max_rounds: int | None = None,
) -> CrawlPageResult:
    """Sync wrapper for browse agent and refresh workers."""
    rounds = max_rounds if max_rounds is not None else _vision_nav_max_rounds()
    return asyncio.run(
        vision_navigate_url(
            url,
            pipeline=pipeline,
            max_rounds=rounds,
        )
    )


def reset_crawl4ai_client_for_tests() -> None:
    """Clear hub-side queue metadata (tests only)."""
    for path in (_waiting_path(), _in_flight_path(), _last_batch_path()):
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            logger.debug("could not remove %s during test reset", path, exc_info=True)
