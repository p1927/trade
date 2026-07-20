"""Backtrader strategy templates for execution simulation."""

from __future__ import annotations

from typing import Any


def run_backtrader_futures_trend(
    signals: list[dict[str, Any]],
    *,
    initial_cash: float = 1_000_000.0,
) -> dict[str, Any]:
    """Simple weekly rebalance on precomputed signal series."""
    try:
        import backtrader as bt
    except ImportError:
        return {"status": "skipped", "reason": "backtrader_not_installed"}

    if not signals:
        return {"status": "error", "reason": "no_signals"}

    class SignalData(bt.feeds.PandasData):
        lines = ("signal",)
        params = (("signal", -1),)

    import pandas as pd

    df = pd.DataFrame(signals)
    df["datetime"] = pd.to_datetime(df["date"])
    df = df.set_index("datetime")
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    df["volume"] = 0

    class FuturesTrend(bt.Strategy):
        params = (("size", 1),)

        def next(self):
            sig = int(self.data.signal[0])
            if sig > 0 and not self.position:
                self.buy(size=self.p.size)
            elif sig < 0 and not self.position:
                self.sell(size=self.p.size)
            elif sig == 0 and self.position:
                self.close()

    cerebro = bt.Cerebro()
    data = SignalData(dataname=df)
    cerebro.adddata(data)
    cerebro.addstrategy(FuturesTrend)
    cerebro.broker.setcash(initial_cash)
    start = cerebro.broker.getvalue()
    cerebro.run()
    end = cerebro.broker.getvalue()
    return {
        "status": "ok",
        "strategy": "futures_trend",
        "start_value": round(start, 2),
        "end_value": round(end, 2),
        "return_pct": round((end - start) / start * 100.0, 4),
        "bars": len(signals),
    }
