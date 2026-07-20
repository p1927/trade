"""Optional pandas-ta indicators — only columns not in technical_features."""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# New keys not covered by technical_features._TECHNICAL_OUTPUT_KEYS
_PANDAS_TA_OUTPUT_KEYS: tuple[str, ...] = (
    "nifty_ichimoku_conv",
    "nifty_ichimoku_base",
    "nifty_supertrend",
    "nifty_keltner_pct_b",
)


def pandas_ta_factor_keys() -> tuple[str, ...]:
    return _PANDAS_TA_OUTPUT_KEYS


def enrich_pandas_ta_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Append pandas-ta columns when library is installed."""
    if frame.empty or "close" not in frame.columns:
        return frame
    try:
        import pandas_ta as ta
    except ImportError:
        logger.debug("pandas-ta not installed — skipping TA enrichment")
        return frame

    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    high = pd.to_numeric(out.get("high", close), errors="coerce")
    low = pd.to_numeric(out.get("low", close), errors="coerce")

    try:
        ich = ta.ichimoku(high, low, close)
        if ich is not None and not ich.empty:
            conv_col = [c for c in ich.columns if "ITS" in c or "conversion" in c.lower()]
            base_col = [c for c in ich.columns if "IKS" in c or "base" in c.lower()]
            if conv_col:
                out["nifty_ichimoku_conv"] = ich[conv_col[0]].values
            if base_col:
                out["nifty_ichimoku_base"] = ich[base_col[0]].values
    except Exception as exc:
        logger.debug("ichimoku failed: %s", exc)

    try:
        st = ta.supertrend(high, low, close, length=10, multiplier=3.0)
        if st is not None and not st.empty:
            st_col = [c for c in st.columns if c.startswith("SUPERT_") and not c.endswith("d")]
            if st_col:
                out["nifty_supertrend"] = st[st_col[0]].values
    except Exception as exc:
        logger.debug("supertrend failed: %s", exc)

    try:
        kc = ta.kc(high, low, close, length=20)
        if kc is not None and not kc.empty:
            upper = [c for c in kc.columns if "KCU" in c]
            lower = [c for c in kc.columns if "KCL" in c]
            if upper and lower:
                span = kc[upper[0]] - kc[lower[0]]
                out["nifty_keltner_pct_b"] = (close - kc[lower[0]]) / span.replace(0, pd.NA)
    except Exception as exc:
        logger.debug("keltner failed: %s", exc)

    return out
