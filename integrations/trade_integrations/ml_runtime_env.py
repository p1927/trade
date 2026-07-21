"""macOS OpenMP (libomp) discovery for LightGBM / XGBoost wheels.

Homebrew may live at /opt/homebrew, /usr/local, or ~/.homebrew. ML wheels
embed @rpath lookups under /opt/homebrew/opt/libomp. Stack startup exports
DYLD_LIBRARY_PATH before spawning Python; ``ensure_prediction_ml.sh`` can also
symlink libomp into /opt/homebrew when writable.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_LIBOMP_LOADED = False
_YFINANCE_WARNING_SUPPRESSED = False
_LIBOMP_RPATH = Path("/opt/homebrew/opt/libomp/lib/libomp.dylib")

_VERIFY_SNIPPET = """
import lightgbm, xgboost, darts, shap, sklearn
print(
    f"lightgbm={lightgbm.__version__}, xgboost={xgboost.__version__}, "
    f"darts={darts.__version__}, shap={shap.__version__}, sklearn={sklearn.__version__}"
)
"""


def _libomp_paths() -> list[str]:
    candidates: list[str] = []
    libomp_lib = (os.environ.get("LIBOMP_LIB") or "").strip()
    if libomp_lib:
        candidates.append(os.path.join(libomp_lib, "libomp.dylib"))
        if libomp_lib.endswith(".dylib"):
            candidates[-1] = libomp_lib

    prefix = (os.environ.get("HOMEBREW_PREFIX") or "").strip()
    search_dirs = [
        f"{prefix}/opt/libomp/lib" if prefix else "",
        "/opt/homebrew/opt/libomp/lib",
        "/usr/local/opt/libomp/lib",
        os.path.expanduser("~/.homebrew/opt/libomp/lib"),
    ]
    for libdir in search_dirs:
        if libdir:
            candidates.append(os.path.join(libdir, "libomp.dylib"))
    return candidates


def resolve_libomp_libdir() -> str | None:
    """Return the directory containing libomp.dylib, if installed."""
    for path in _libomp_paths():
        if path and os.path.isfile(path):
            return os.path.dirname(path)
    return None


def ensure_libomp_symlink() -> tuple[bool, str]:
    """Symlink Homebrew libomp into /opt/homebrew so ML wheels find @rpath/libomp."""
    if sys.platform != "darwin":
        return True, "not required on this OS"

    if _LIBOMP_RPATH.is_file():
        return True, f"libomp present at {_LIBOMP_RPATH}"

    libdir = resolve_libomp_libdir()
    if not libdir:
        return False, "libomp not installed — run: brew install libomp"

    candidate = Path(libdir).parent  # .../opt/libomp
    link = Path("/opt/homebrew/opt/libomp")
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            try:
                if link.resolve() == candidate.resolve():
                    return True, f"libomp linked at {link}"
            except OSError:
                pass
        link.symlink_to(candidate)
        if _LIBOMP_RPATH.is_file():
            return True, f"linked {candidate} -> {link}"
        return False, f"symlink created but {_LIBOMP_RPATH} still missing"
    except OSError as exc:
        return False, (
            f"could not link {link} -> {candidate}: {exc} "
            "(stack will use DYLD_LIBRARY_PATH when spawning Python)"
        )


def ml_runtime_env() -> dict[str, str]:
    """Return env vars that must be set before spawning Python for ML imports."""
    env = dict(os.environ)
    libdir = resolve_libomp_libdir()
    if sys.platform == "darwin" and libdir:
        env["LIBOMP_LIB"] = libdir
        existing = env.get("DYLD_LIBRARY_PATH", "")
        parts = [p for p in existing.split(":") if p]
        if libdir not in parts:
            parts.insert(0, libdir)
        env["DYLD_LIBRARY_PATH"] = ":".join(parts)
        env.setdefault("YF_DISABLE_CURL_CFFI", "1")
    return env


def _yfinance_curl_cffi_disabled() -> bool:
    return os.environ.get("YF_DISABLE_CURL_CFFI", "").lower() in ("1", "true", "yes")


def _configure_yfinance_requests_fallback() -> None:
    """Prefer requests over curl_cffi for yfinance when libomp DYLD is injected on macOS.

    curl_cffi bundles BoringSSL; prepending libomp to DYLD_LIBRARY_PATH can break its
    TLS handshake (curl error 35 / OPENSSL_internal invalid library). yfinance reads
    YF_DISABLE_CURL_CFFI at import time and falls back to requests.
    """
    if sys.platform != "darwin":
        return
    os.environ.setdefault("YF_DISABLE_CURL_CFFI", "1")


def _suppress_yfinance_curl_fallback_warning() -> None:
    """Silence yfinance's fallback warning when curl_cffi is intentionally disabled."""
    global _YFINANCE_WARNING_SUPPRESSED
    if _YFINANCE_WARNING_SUPPRESSED or not _yfinance_curl_cffi_disabled():
        return
    try:
        import yfinance._http as yf_http

        yf_http._warn_once_on_fallback = lambda: None
    except ImportError:
        pass
    _YFINANCE_WARNING_SUPPRESSED = True


def ensure_libomp_loaded() -> bool:
    """Best-effort libomp configure for the current process (may be insufficient on macOS)."""
    global _LIBOMP_LOADED
    if _LIBOMP_LOADED or sys.platform != "darwin":
        return _LIBOMP_LOADED
    libdir = resolve_libomp_libdir()
    if not libdir:
        return False
    os.environ.setdefault("LIBOMP_LIB", libdir)
    existing = os.environ.get("DYLD_LIBRARY_PATH", "")
    if libdir not in existing.split(":"):
        os.environ["DYLD_LIBRARY_PATH"] = f"{libdir}:{existing}" if existing else libdir
    _configure_yfinance_requests_fallback()
    _suppress_yfinance_curl_fallback_warning()
    _LIBOMP_LOADED = True
    return True


def prepare_yfinance_runtime() -> bool:
    """Configure libomp and yfinance before the first ``yfinance`` import in this process."""
    loaded = ensure_libomp_loaded()
    _suppress_yfinance_curl_fallback_warning()
    return loaded


def _verify_imports_in_process() -> tuple[bool, str]:
    errors: list[str] = []
    versions: list[str] = []
    for mod in ("lightgbm", "xgboost", "darts", "shap", "sklearn"):
        try:
            imported = __import__(mod)
            version = getattr(imported, "__version__", "?")
            versions.append(f"{mod}={version}")
        except Exception as exc:
            errors.append(f"{mod}: {exc}")
    if errors:
        return False, "; ".join(errors) + " — run: ./scripts/ensure_prediction_ml.sh"
    return True, ", ".join(versions)


def verify_prediction_ml() -> tuple[bool, str]:
    """Verify forecast-lab ML imports using the same env stack startup applies."""
    if sys.platform == "darwin":
        libdir = resolve_libomp_libdir()
        if not libdir:
            return (
                False,
                "libomp not found (macOS) — run: brew install libomp "
                "then: ./scripts/ensure_prediction_ml.sh",
            )
        ensure_libomp_symlink()

    if sys.platform != "darwin":
        return _verify_imports_in_process()

    ok, message = _verify_imports_in_process()
    if ok:
        return ok, message

    env = ml_runtime_env()
    proc = subprocess.run(
        [sys.executable, "-c", _VERIFY_SNIPPET],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return True, (proc.stdout or message).strip()
    detail = (proc.stderr or proc.stdout or message).strip()
    return False, detail + " — run: ./scripts/ensure_prediction_ml.sh"
