"""HTTP client seeded from nodriver cookies — curl_cffi preferred, requests fallback."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

from trade_integrations.http import scoped_session
from trade_integrations.nse_browser.registry import cookies_path
from trade_integrations.nse_browser.session import RateLimiter, cookies_to_requests_jar, load_cookies

try:
    from trade_integrations.nse_browser.session import INTRA_MISSION_INTERVAL_S
except ImportError:
    INTRA_MISSION_INTERVAL_S = 5.0

logger = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


class HttpBridge:
    """Download URLs using browser-exported cookies and TLS impersonation when available."""

    def __init__(self, cookies: list[dict[str, Any]] | None = None, *, min_interval_s: float | None = None) -> None:
        self.cookies = cookies if cookies is not None else load_cookies()
        interval = INTRA_MISSION_INTERVAL_S if min_interval_s is None else min_interval_s
        self.rate_limiter = RateLimiter(min_interval_s=interval)
        self._use_cffi = False
        try:
            from curl_cffi import requests as cffi_requests  # noqa: F401

            self._use_cffi = True
        except ImportError:
            self._use_cffi = False

    def get_text(self, url: str, *, referer: str | None = None, retries: int = 2) -> tuple[int, str]:
        for attempt in range(retries + 1):
            status, body = self._request(url, referer=referer)
            if status == 200:
                return status, body
            if status in {403, 429} and attempt < retries:
                time.sleep(min(60.0, 5.0 * (2**attempt)))
                continue
            return status, body
        return 0, ""

    def get_bytes(self, url: str, *, referer: str | None = None, retries: int = 2) -> tuple[int, bytes]:
        for attempt in range(retries + 1):
            status, body = self._request_bytes(url, referer=referer)
            if status == 200:
                return status, body
            if status in {403, 429} and attempt < retries:
                time.sleep(min(60.0, 5.0 * (2**attempt)))
                continue
            return status, body
        return 0, b""

    def _request_bytes(self, url: str, *, referer: str | None) -> tuple[int, bytes]:
        host = urlparse(url).netloc or "nseindia.com"
        self.rate_limiter.wait_if_needed(host)
        headers = dict(_BROWSER_HEADERS)
        if referer:
            headers["Referer"] = referer

        if self._use_cffi:
            return self._request_cffi_bytes(url, headers)
        return self._request_requests_bytes(url, headers)

    def _request_cffi_bytes(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        from curl_cffi import requests as cffi_requests

        jar = cookies_to_requests_jar(self.cookies)
        try:
            resp = cffi_requests.get(
                url,
                headers=headers,
                cookies=jar,
                impersonate="chrome120",
                timeout=45,
            )
            content = resp.content
            return resp.status_code, content if isinstance(content, bytes) else b""
        except Exception as exc:
            logger.debug("curl_cffi GET bytes failed %s: %s", url, exc)
            return 0, b""

    def _request_requests_bytes(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        try:
            with scoped_session(headers=headers) as session:
                session.cookies = cookies_to_requests_jar(self.cookies)
                if not self.cookies and "nseindia.com" in url:
                    session.get("https://www.nseindia.com", timeout=15)
                resp = session.get(url, timeout=45)
                content = resp.content
                return resp.status_code, content if isinstance(content, bytes) else b""
        except Exception as exc:
            logger.debug("requests GET bytes failed %s: %s", url, exc)
            return 0, b""

    def _request(self, url: str, *, referer: str | None) -> tuple[int, str]:
        host = urlparse(url).netloc or "nseindia.com"
        self.rate_limiter.wait_if_needed(host)
        headers = dict(_BROWSER_HEADERS)
        if referer:
            headers["Referer"] = referer

        if self._use_cffi:
            return self._request_cffi(url, headers)
        return self._request_requests(url, headers)

    def _request_cffi(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        from curl_cffi import requests as cffi_requests

        jar = cookies_to_requests_jar(self.cookies)
        try:
            resp = cffi_requests.get(
                url,
                headers=headers,
                cookies=jar,
                impersonate="chrome120",
                timeout=45,
            )
            return resp.status_code, resp.text
        except Exception as exc:
            logger.debug("curl_cffi GET failed %s: %s", url, exc)
            return 0, ""

    def _request_requests(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        try:
            with scoped_session(headers=headers) as session:
                session.cookies = cookies_to_requests_jar(self.cookies)
                if not self.cookies and "nseindia.com" in url:
                    session.get("https://www.nseindia.com", timeout=15)
                resp = session.get(url, timeout=45)
                return resp.status_code, resp.text
        except Exception as exc:
            logger.debug("requests GET failed %s: %s", url, exc)
            return 0, ""

    @staticmethod
    def has_persisted_cookies() -> bool:
        return cookies_path().is_file() and bool(load_cookies())
