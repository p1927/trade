"""Ensure Google Chrome / Chromium is available for nodriver."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


def find_chrome_binary() -> str | None:
    override = os.environ.get("NSE_BROWSER_CHROME_PATH", "").strip()
    if override and Path(override).is_file():
        return override
    for path in _CHROME_CANDIDATES:
        if Path(path).is_file():
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def install_chrome() -> bool:
    """Attempt OS-specific Chrome install. Returns True if binary exists after attempt."""
    system = platform.system()
    logger.info("Chrome not found — attempting install on %s", system)
    try:
        if system == "Darwin":
            if shutil.which("brew"):
                subprocess.run(
                    ["brew", "install", "--cask", "google-chrome"],
                    check=False,
                    timeout=600,
                )
            else:
                logger.error("Homebrew required to auto-install Chrome on macOS")
                return False
        elif system == "Linux":
            if shutil.which("apt-get"):
                subprocess.run(
                    [
                        "sudo",
                        "apt-get",
                        "update",
                        "-qq",
                    ],
                    check=False,
                    timeout=120,
                )
                subprocess.run(
                    [
                        "sudo",
                        "apt-get",
                        "install",
                        "-y",
                        "wget",
                        "gnupg",
                    ],
                    check=False,
                    timeout=120,
                )
                subprocess.run(
                    [
                        "wget",
                        "-q",
                        "-O",
                        "-",
                        "https://dl.google.com/linux/linux_signing_key.pub",
                    ],
                    check=False,
                )
                subprocess.run(
                    [
                        "sudo",
                        "apt-get",
                        "install",
                        "-y",
                        "google-chrome-stable",
                    ],
                    check=False,
                    timeout=300,
                )
            elif shutil.which("dnf"):
                subprocess.run(
                    ["sudo", "dnf", "install", "-y", "google-chrome-stable"],
                    check=False,
                    timeout=300,
                )
        else:
            logger.error("Auto Chrome install not supported on %s", system)
            return False
    except Exception as exc:
        logger.warning("Chrome install attempt failed: %s", exc)
    return find_chrome_binary() is not None


def ensure_chrome(*, auto_install: bool | None = None) -> str:
    """
    Return path to Chrome binary. Optionally install if missing.

    Set NSE_BROWSER_AUTO_INSTALL_CHROME=1 to enable auto-install (default: try on first use).
    """
    found = find_chrome_binary()
    if found:
        os.environ.setdefault("NSE_BROWSER_CHROME_PATH", found)
        return found

    if auto_install is None:
        auto_install = os.environ.get("NSE_BROWSER_AUTO_INSTALL_CHROME", "1").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    if auto_install and install_chrome():
        found = find_chrome_binary()
        if found:
            os.environ.setdefault("NSE_BROWSER_CHROME_PATH", found)
            return found

    raise RuntimeError(
        "Google Chrome not found. Install manually or set NSE_BROWSER_CHROME_PATH. "
        "macOS: brew install --cask google-chrome"
    )


def ensure_chrome_or_warn() -> str | None:
    try:
        return ensure_chrome()
    except RuntimeError as exc:
        logger.warning("%s", exc)
        return None
