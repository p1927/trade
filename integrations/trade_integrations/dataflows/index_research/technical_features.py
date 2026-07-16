"""Nifty OHLCV-derived technical features for index prediction."""

from __future__ import annotations

import numpy as np
import pandas as pd

_TECHNICAL_OUTPUT_KEYS: tuple[str, ...] = (
    "nifty_return_7d",
    "nifty_return_14d",
    "nifty_rsi_14",
    "nifty_realized_vol_20d",
    "nifty_ma20_distance_pct",
    "nifty_ma50_distance_pct",
    "nifty_ma200_distance_pct",
    "nifty_macd_line",
    "nifty_macd_signal",
    "nifty_macd_histogram",
    "nifty_bb_percent_b",
    "nifty_bb_width_pct",
    "nifty_stoch_k",
    "nifty_stoch_d",
    "nifty_williams_r",
    "nifty_cci_20",
    "nifty_adx_14",
    "nifty_atr_pct",
    "nifty_golden_cross_signal",
)


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder-style RSI on close prices."""
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_return_pct(close: pd.Series, days: int) -> pd.Series:
    """Backward-looking return (%) over ``days``."""
    shifted = close.astype(float).shift(days)
    return (close.astype(float) - shifted) / shifted.replace(0, np.nan) * 100.0


def compute_realized_vol(close: pd.Series, window: int = 20) -> pd.Series:
    """Annualized realized vol (%) from daily log returns."""
    log_ret = np.log(close.astype(float) / close.astype(float).shift(1))
    daily_std = log_ret.rolling(window=window, min_periods=max(3, window // 2)).std()
    return daily_std * np.sqrt(252.0) * 100.0


def compute_ma_distance_pct(close: pd.Series, window: int = 20) -> pd.Series:
    """Distance (%) of close from simple moving average."""
    ma = close.astype(float).rolling(window=window, min_periods=max(3, window // 2)).mean()
    return (close.astype(float) - ma) / ma.replace(0, np.nan) * 100.0


def compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.astype(float).ewm(span=span, adjust=False).mean()


def compute_macd_components(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, and histogram."""
    ema12 = compute_ema(close, 12)
    ema26 = compute_ema(close, 26)
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal
    return macd, signal, histogram


def compute_macd_histogram(close: pd.Series) -> pd.Series:
    _, _, histogram = compute_macd_components(close)
    return histogram


def compute_bb_bands(close: pd.Series, window: int = 20, std_dev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    ma = close.astype(float).rolling(window=window, min_periods=max(3, window // 2)).mean()
    std = close.astype(float).rolling(window=window, min_periods=max(3, window // 2)).std()
    upper = ma + std_dev * std
    lower = ma - std_dev * std
    return upper, ma, lower


def compute_bb_width_pct(close: pd.Series, window: int = 20, std_dev: float = 2.0) -> pd.Series:
    upper, middle, lower = compute_bb_bands(close, window=window, std_dev=std_dev)
    width = (upper - lower) / middle.replace(0, np.nan) * 100.0
    return width


def compute_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    *,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    lowest_low = low.astype(float).rolling(k_period, min_periods=max(3, k_period // 2)).min()
    highest_high = high.astype(float).rolling(k_period, min_periods=max(3, k_period // 2)).max()
    span = (highest_high - lowest_low).replace(0, np.nan)
    stoch_k = (close.astype(float) - lowest_low) / span * 100.0
    stoch_d = stoch_k.rolling(d_period, min_periods=1).mean()
    return stoch_k.fillna(50.0), stoch_d.fillna(50.0)


def compute_williams_r(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    highest_high = high.astype(float).rolling(period, min_periods=max(3, period // 2)).max()
    lowest_low = low.astype(float).rolling(period, min_periods=max(3, period // 2)).min()
    span = (highest_high - lowest_low).replace(0, np.nan)
    return ((highest_high - close.astype(float)) / span * -100.0).fillna(-50.0)


def compute_cci(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
) -> pd.Series:
    typical = (high.astype(float) + low.astype(float) + close.astype(float)) / 3.0
    sma = typical.rolling(period, min_periods=max(3, period // 2)).mean()
    mad = typical.rolling(period, min_periods=max(3, period // 2)).apply(
        lambda x: np.abs(x - x.mean()).mean(),
        raw=True,
    )
    return ((typical - sma) / (0.015 * mad.replace(0, np.nan))).fillna(0.0)


def compute_bb_percent_b(close: pd.Series, window: int = 20, std_dev: float = 2.0) -> pd.Series:
    ma = close.astype(float).rolling(window=window, min_periods=max(3, window // 2)).mean()
    std = close.astype(float).rolling(window=window, min_periods=max(3, window // 2)).std()
    upper = ma + std_dev * std
    lower = ma - std_dev * std
    width = (upper - lower).replace(0, np.nan)
    return (close.astype(float) - lower) / width


def compute_atr_pct(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    prev_close = close.astype(float).shift(1)
    tr = pd.concat(
        [
            (high.astype(float) - low.astype(float)).abs(),
            (high.astype(float) - prev_close).abs(),
            (low.astype(float) - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window=period, min_periods=max(3, period // 2)).mean()
    return atr / close.astype(float).replace(0, np.nan) * 100.0


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    up_move = high.astype(float).diff()
    down_move = -low.astype(float).diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=close.index)
    minus_dm = pd.Series(minus_dm, index=close.index)

    atr = compute_atr_pct(high, low, close, period) * close.astype(float) / 100.0
    atr = atr.replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100.0
    return dx.rolling(period, min_periods=max(3, period // 2)).mean()


def compute_golden_cross_signal(close: pd.Series) -> pd.Series:
    ma50 = close.astype(float).rolling(50, min_periods=20).mean()
    ma200 = close.astype(float).rolling(200, min_periods=60).mean()
    signal = pd.Series(0.0, index=close.index)
    signal = signal.mask(ma50 > ma200, 1.0)
    signal = signal.mask(ma50 < ma200, -1.0)
    return signal


def enrich_nifty_technical_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add technical feature columns from OHLC history."""
    if frame.empty or "close" not in frame.columns:
        return frame

    out = frame.copy()
    close = out["close"].astype(float)
    high = out["high"].astype(float) if "high" in out.columns else close
    low = out["low"].astype(float) if "low" in out.columns else close

    out["nifty_return_7d"] = compute_return_pct(close, 7)
    out["nifty_return_14d"] = compute_return_pct(close, 14)
    out["nifty_rsi_14"] = compute_rsi(close, 14)
    out["nifty_realized_vol_20d"] = compute_realized_vol(close, 20)
    out["nifty_ma20_distance_pct"] = compute_ma_distance_pct(close, 20)
    out["nifty_ma50_distance_pct"] = compute_ma_distance_pct(close, 50)
    out["nifty_ma200_distance_pct"] = compute_ma_distance_pct(close, 200)
    macd_line, macd_signal, macd_hist = compute_macd_components(close)
    out["nifty_macd_line"] = macd_line
    out["nifty_macd_signal"] = macd_signal
    out["nifty_macd_histogram"] = macd_hist
    out["nifty_bb_percent_b"] = compute_bb_percent_b(close)
    out["nifty_bb_width_pct"] = compute_bb_width_pct(close)
    stoch_k, stoch_d = compute_stochastic(high, low, close)
    out["nifty_stoch_k"] = stoch_k
    out["nifty_stoch_d"] = stoch_d
    out["nifty_williams_r"] = compute_williams_r(high, low, close)
    out["nifty_cci_20"] = compute_cci(high, low, close)
    out["nifty_atr_pct"] = compute_atr_pct(high, low, close)
    out["nifty_adx_14"] = compute_adx(high, low, close)
    out["nifty_golden_cross_signal"] = compute_golden_cross_signal(close)
    return out


def latest_technical_factor_dict(frame: pd.DataFrame) -> dict[str, float]:
    """Return the most recent row's technical features as a flat dict."""
    enriched = enrich_nifty_technical_columns(frame)
    if enriched.empty:
        return {}

    row = enriched.iloc[-1]
    out: dict[str, float] = {}
    for key in _TECHNICAL_OUTPUT_KEYS:
        if key not in row.index:
            continue
        value = row[key]
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        out[key] = float(value)
    return out


def technical_factor_rows(frame: pd.DataFrame) -> list[dict]:
    """Build factor rows from the latest technical snapshot."""
    latest = latest_technical_factor_dict(frame)
    return [
        {"factor": name, "value": value, "source": "nifty_technical"}
        for name, value in latest.items()
    ]
