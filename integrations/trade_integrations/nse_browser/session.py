"""nodriver session lifecycle, cookie persistence, CAPTCHA handling, rate limiting."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from trade_integrations.nse_browser.chrome_bootstrap import ensure_chrome_or_warn
from trade_integrations.nse_browser.download_manager import DownloadManager
from trade_integrations.nse_browser.network_sniffer import NetworkSniffer
from trade_integrations.nse_browser.registry import cookies_path, session_dir

logger = logging.getLogger(__name__)

DEFAULT_MIN_INTERVAL_S = float(os.environ.get("NSE_BROWSER_MIN_INTERVAL_S", "180"))
INTRA_MISSION_INTERVAL_S = float(os.environ.get("NSE_BROWSER_INTRA_MISSION_INTERVAL_S", "1"))
PAGE_WAIT_S = float(os.environ.get("NSE_BROWSER_PAGE_WAIT_S", "0.8"))
MISSION_TIMEOUT_S = float(os.environ.get("NSE_BROWSER_MISSION_TIMEOUT_S", "55"))
HISTORICAL_MISSION_TIMEOUT_S = float(os.environ.get("NSE_BROWSER_HISTORICAL_TIMEOUT_S", "120"))
CAPTCHA_SLEEP_S = float(os.environ.get("NSE_BROWSER_CAPTCHA_SLEEP_S", "1.0"))
ALLOW_DOWNLOAD_CLICK = os.environ.get("NSE_BROWSER_ALLOW_DOWNLOAD_CLICK", "0").strip().lower() in {
    "1",
    "true",
    "yes",
}

CAPTCHA_MARKERS = (
    "captcha",
    "verify you are human",
    "are you a robot",
    "i'm not a robot",
    "press & hold",
    "unusual traffic from your computer",
    "access denied",
    "bot detection",
    "challenge-platform",
    "cf-challenge",
    "hcaptcha",
)


@dataclass
class RateLimiter:
    """Enforce minimum interval between requests to one host."""

    min_interval_s: float = DEFAULT_MIN_INTERVAL_S
    _last_by_host: dict[str, float] = field(default_factory=dict)

    def wait_if_needed(self, host: str) -> None:
        now = time.monotonic()
        last = self._last_by_host.get(host, 0.0)
        elapsed = now - last
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last_by_host[host] = time.monotonic()


def detect_captcha(html: str) -> bool:
    lower = (html or "").lower()
    if not lower:
        return False
    strong = (
        "verify you are human",
        "are you a robot",
        "i'm not a robot",
        "press & hold",
        "cf-challenge",
        "hcaptcha",
        "challenge-platform",
    )
    if any(s in lower for s in strong):
        return True
    hits = sum(1 for marker in CAPTCHA_MARKERS if marker in lower)
    return hits >= 2


def load_cookies() -> list[dict[str, Any]]:
    path = cookies_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "cookies" in payload:
        return list(payload["cookies"])
    return []


def save_cookies(cookies: list[dict[str, Any]]) -> None:
    session_dir().mkdir(parents=True, exist_ok=True)
    body = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "cookies": cookies,
    }
    cookies_path().write_text(json.dumps(body, indent=2), encoding="utf-8")


def cookies_to_requests_jar(cookies: list[dict[str, Any]]):
    from trade_integrations.http import cookie_jar

    jar = cookie_jar()
    for item in cookies:
        name = item.get("name")
        value = item.get("value")
        domain = item.get("domain") or ".nseindia.com"
        if name and value is not None:
            jar.set(name, value, domain=domain, path=item.get("path") or "/")
    return jar


def visible_text_from_html(html: str, *, limit: int = 20000) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


async def _export_tab_cookies(tab) -> list[dict[str, Any]]:
    try:
        cdp = await tab.send("Network.getAllCookies")
        raw = cdp.get("cookies") if isinstance(cdp, dict) else []
        if raw:
            return [
                {
                    "name": c.get("name"),
                    "value": c.get("value"),
                    "domain": c.get("domain"),
                    "path": c.get("path"),
                }
                for c in raw
                if c.get("name")
            ]
    except Exception as exc:
        logger.debug("CDP cookie export failed: %s", exc)

    try:
        js_cookies = await tab.evaluate("document.cookie")
        if not js_cookies:
            return []
        out: list[dict[str, Any]] = []
        for part in str(js_cookies).split(";"):
            if "=" not in part:
                continue
            name, value = part.strip().split("=", 1)
            out.append({"name": name, "value": value, "domain": ".nseindia.com", "path": "/"})
        return out
    except Exception as exc:
        logger.debug("JS cookie export failed: %s", exc)
        return []


async def _configure_tab(session: NodriverSession, tab) -> None:
    """Wire download manager + network sniffer on every new tab."""
    if tab is None:
        return
    await session.downloads.configure(tab)
    await session.network_sniffer.attach(tab)


async def resolve_bot_challenge(tab) -> bool:
    """
    Attempt automated bot/CAPTCHA resolution via nodriver built-ins.

    Uses cf_verify/verify_cf (requires opencv-python) and text-based checkbox clicks.
    """
    resolved = False
    for attempt in range(3):
        for method_name in ("cf_verify", "verify_cf"):
            method = getattr(tab, method_name, None)
            if method is None:
                continue
            try:
                result = await method()
                if result:
                    logger.info("CAPTCHA resolved via %s (attempt %s)", method_name, attempt + 1)
                    resolved = True
                    await asyncio.sleep(CAPTCHA_SLEEP_S)
                    break
            except Exception as exc:
                logger.debug("%s failed: %s", method_name, exc)
        if resolved:
            break

        for needle in (
            "verify you are human",
            "i'm not a robot",
            "press & hold",
            "continue",
        ):
            try:
                el = await tab.find(needle, best_match=True)
                if el:
                    await el.click()
                    await asyncio.sleep(CAPTCHA_SLEEP_S)
                    logger.info("Clicked challenge element matching %r", needle)
                    resolved = True
                    break
            except Exception:
                continue
        if resolved:
            break
        await asyncio.sleep(CAPTCHA_SLEEP_S)

    if resolved:
        try:
            await tab
        except Exception:
            pass
    return resolved


async def _start_browser(*, headless: bool):
    import nodriver as uc

    chrome_path = ensure_chrome_or_warn()
    kwargs: dict[str, Any] = {"headless": headless}
    if chrome_path:
        kwargs["browser_executable_path"] = chrome_path
    return await uc.start(**kwargs)


async def _bootstrap_nodriver(
    url: str,
    *,
    headless: bool,
    session: NodriverSession | None = None,
) -> tuple[Any, Any, list[dict[str, Any]], str]:
    browser = await _start_browser(headless=headless)
    tab = await browser.get(url)
    if session is not None:
        await _configure_tab(session, tab)
    await asyncio.sleep(PAGE_WAIT_S)
    html = ""
    try:
        html = await tab.get_content()
    except Exception:
        pass
    if detect_captcha(html):
        if await resolve_bot_challenge(tab):
            try:
                html = await tab.get_content()
            except Exception:
                pass
    cookies = await _export_tab_cookies(tab)
    if cookies:
        save_cookies(cookies)
    return browser, tab, cookies, html


class NodriverSession:
    """
    Context manager wrapping nodriver for one mission or batch run.

    NSE blocks repeated open/close cycles — reuse one instance per backfill batch:
    navigate with goto() inside a single ``async with NodriverSession(...)`` block.
    """

    def __init__(
        self,
        *,
        headless: bool | None = None,
        refresh_cookies: bool = False,
        close_on_exit: bool = True,
    ) -> None:
        env_headless = os.environ.get("NSE_BROWSER_HEADLESS", "0").strip() in {"1", "true", "yes"}
        self.headless = env_headless if headless is None else headless
        self.refresh_cookies = refresh_cookies
        self.close_on_exit = close_on_exit
        self.browser = None
        self.tab = None
        self.cookies: list[dict[str, Any]] = []
        self.last_html = ""
        self.last_visible_text = ""
        self.captcha_detected = False
        self.captcha_resolved = False
        self.rate_limiter = RateLimiter(min_interval_s=INTRA_MISSION_INTERVAL_S)
        self.downloads = DownloadManager()
        self.network_sniffer = NetworkSniffer()

    async def __aenter__(self) -> NodriverSession:
        if not self.refresh_cookies:
            self.cookies = load_cookies()
            if self.cookies:
                return self
        try:
            self.browser, self.tab, self.cookies, self.last_html = await _bootstrap_nodriver(
                "https://www.nseindia.com",
                headless=self.headless,
                session=self,
            )
            self.last_visible_text = visible_text_from_html(self.last_html)
            self.captcha_detected = detect_captcha(self.last_html)
            self.captcha_resolved = not self.captcha_detected
        except ImportError:
            logger.warning("nodriver not installed; using persisted cookies only")
            self.cookies = load_cookies()
        except Exception as exc:
            logger.warning("nodriver bootstrap failed: %s", exc)
            self.cookies = load_cookies()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.browser is not None and self.close_on_exit:
            try:
                self.browser.stop()
            except Exception:
                pass
            self.browser = None
            self.tab = None

    async def _ensure_browser(self, url: str) -> None:
        if self.tab is not None:
            return
        if not self.cookies and not self.refresh_cookies:
            self.cookies = load_cookies()
        if self.browser is None:
            self.browser = await _start_browser(headless=self.headless)
        self.tab = await self.browser.get(url)
        await _configure_tab(self, self.tab)

    async def goto(self, url: str, *, resolve_captcha: bool = True) -> str:
        from urllib.parse import urlparse

        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"invalid goto url: {url!r}")

        host = urlparse(url).netloc or "nseindia.com"
        self.rate_limiter.wait_if_needed(host)
        self.network_sniffer.clear()

        if self.tab is None:
            await self._ensure_browser(url)
        else:
            if self.browser is None:
                self.browser = await _start_browser(headless=self.headless)
            await self.network_sniffer.attach(self.tab)
            try:
                self.tab = await self.tab.get(url)
            except Exception as exc:
                logger.debug("tab.get failed for %s: %s — recreating tab", url[:80], exc)
                self.tab = await self.browser.get(url)
                await _configure_tab(self, self.tab)

        await asyncio.sleep(PAGE_WAIT_S)
        try:
            self.last_html = await self.tab.get_content()
        except Exception:
            self.last_html = ""
        self.last_visible_text = visible_text_from_html(self.last_html)
        self.captcha_detected = detect_captcha(self.last_html)
        if self.captcha_detected and resolve_captcha and self.tab is not None:
            if await resolve_bot_challenge(self.tab):
                self.captcha_resolved = True
                try:
                    self.last_html = await self.tab.get_content()
                    self.last_visible_text = visible_text_from_html(self.last_html)
                    self.captcha_detected = detect_captcha(self.last_html)
                except Exception:
                    pass
            else:
                self.captcha_resolved = False
        else:
            self.captcha_resolved = not self.captcha_detected

        fresh = await _export_tab_cookies(self.tab)
        if fresh:
            self.cookies = fresh
            save_cookies(fresh)
        return self.last_html

    async def trigger_mission_csv_downloads(
        self,
        *,
        download_dir=None,
        max_clicks: int = 5,
    ) -> list[str]:
        """Force silent CSV downloads for missions (bypasses ALLOW_DOWNLOAD_CLICK env)."""
        from pathlib import Path

        if self.tab is None:
            return []
        if download_dir is not None:
            self.downloads = DownloadManager(directory=Path(download_dir))
        if not self.downloads._configured:
            await self.downloads.configure(self.tab)

        saved: list[str] = []
        script = """
        () => {
          const els = Array.from(document.querySelectorAll('a, button, [role="button"]'))
            .filter(el => /download/i.test(el.textContent || '') && /\\.csv|csv/i.test(el.textContent || ''));
          return els.map((el, i) => ({ i, text: (el.textContent || '').trim().slice(0, 100) }));
        }
        """
        try:
            candidates = await self.tab.evaluate(script)
        except Exception:
            candidates = []

        if not isinstance(candidates, list) or not candidates:
            paths = await self.trigger_csv_download(force=True)
            return paths

        for item in candidates[:max_clicks]:
            idx = item.get("i") if isinstance(item, dict) else None
            if idx is None:
                continue
            try:

                async def _click_index(i: int = int(idx)) -> None:
                    await self.tab.evaluate(
                        f"""
                        () => {{
                          const els = Array.from(document.querySelectorAll('a, button, [role="button"]'))
                            .filter(el => /download/i.test(el.textContent || '') && /\\.csv|csv/i.test(el.textContent || ''));
                          if (els[{i}]) els[{i}].click();
                        }}
                        """
                    )

                path = await self.downloads.trigger_and_wait(_click_index, timeout_s=30)
                if path:
                    saved.append(str(path))
            except Exception as exc:
                logger.debug("mission csv click %s failed: %s", item, exc)
        return saved

    async def trigger_csv_download(
        self,
        *,
        labels: tuple[str, ...] = ("Download (.csv)", "Download"),
        force: bool = False,
    ) -> list[str]:
        """
        Click download links when CDP silent download is configured.

        Requires NSE_BROWSER_ALLOW_DOWNLOAD_CLICK=1 unless force=True.
        """
        if not force and not ALLOW_DOWNLOAD_CLICK:
            logger.debug("Download click disabled (set NSE_BROWSER_ALLOW_DOWNLOAD_CLICK=1 to enable)")
            return []
        if self.tab is None:
            return []
        if not self.downloads._configured:
            logger.warning("trigger_csv_download called before DownloadManager.configure")
            await self.downloads.configure(self.tab)

        clicked: list[str] = []

        for label in labels:
            try:

                async def _do_click(lbl: str = label) -> None:
                    el = await self.tab.find(lbl, best_match=True)
                    if el:
                        await el.click()

                path = await self.downloads.trigger_and_wait(_do_click)
                if path:
                    clicked.append(str(path))
            except Exception as exc:
                logger.debug("trigger_csv_download %r failed: %s", label, exc)
        return clicked

    async def find_csv_hrefs(self) -> list[str]:
        if self.tab is None:
            return []
        script = """
        () => Array.from(document.querySelectorAll('a[href]'))
          .map(a => a.href)
          .filter(h => h && (h.includes('.csv') || h.toLowerCase().includes('download') || h.includes('/api/')))
        """
        try:
            hrefs = await self.tab.evaluate(script)
            if isinstance(hrefs, list):
                return [str(h) for h in hrefs if h]
        except Exception as exc:
            logger.debug("find_csv_hrefs failed: %s", exc)
        return []

    async def find_links_by_keywords(self, keywords: tuple[str, ...]) -> list[str]:
        if self.tab is None:
            return []
        kw_json = json.dumps([k.lower() for k in keywords])
        script = f"""
        () => {{
          const kws = {kw_json};
          return Array.from(document.querySelectorAll('a'))
            .filter(a => {{
              const t = (a.textContent || '').toLowerCase();
              return kws.some(k => t.includes(k));
            }})
            .map(a => a.href)
            .filter(Boolean);
        }}
        """
        try:
            hrefs = await self.tab.evaluate(script)
            if isinstance(hrefs, list):
                return [str(h) for h in hrefs if h]
        except Exception as exc:
            logger.debug("find_links_by_keywords failed: %s", exc)
        return []

    async def scroll_down(self, *, pixels: int = 600) -> None:
        if self.tab is None:
            return
        try:
            await self.tab.evaluate(f"window.scrollBy(0, {pixels})")
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug("scroll_down failed: %s", exc)

    async def click_target(self, target: str) -> bool:
        if self.tab is None or not target:
            return False
        try:
            if target.startswith("#") or target.startswith(".") or target.startswith("["):
                el = await self.tab.select(target)
            else:
                el = await self.tab.find(target, best_match=True)
            if el:
                await el.click()
                await asyncio.sleep(PAGE_WAIT_S)
                return True
        except Exception as exc:
            logger.debug("click_target %r failed: %s", target, exc)
        return False

    async def screenshot_png(self) -> bytes | None:
        if self.tab is None:
            return None
        try:
            data = await self.tab.save_screenshot(format="png")
            if isinstance(data, bytes):
                return data
            if isinstance(data, str):
                import base64

                return base64.b64decode(data)
        except Exception as exc:
            logger.debug("screenshot_png failed: %s", exc)
        return None


def run_async(coro):
    return asyncio.run(coro)


def run_mission_async(coro, *, timeout_s: float | None = None):
    timeout = MISSION_TIMEOUT_S if timeout_s is None else timeout_s
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


async def run_in_shared_session(coro_factory, *, refresh_cookies: bool = False):
    """
    Run an async callable inside one browser session (avoids NSE block on reopen).

    ``coro_factory`` receives the live ``NodriverSession`` and must not open another.
    """
    async with NodriverSession(refresh_cookies=refresh_cookies) as session:
        if session.tab is None:
            await session.goto("https://www.nseindia.com")
        return await coro_factory(session)
