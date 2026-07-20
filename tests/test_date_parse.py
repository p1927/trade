"""Tests for explicit NSE/historic date parsing."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.hub_storage.date_parse import (
    format_date_series,
    format_datetime_series,
    parse_date_scalar,
    parse_date_series,
)


@pytest.mark.unit
def test_parse_date_scalar_nse_formats() -> None:
    assert parse_date_scalar("15-Jan-2024") == "2024-01-15"
    assert parse_date_scalar("2024-01-15") == "2024-01-15"
    assert parse_date_scalar("15_01_24") == "2024-01-15"
    assert parse_date_scalar("Jan. 15, 2024") == "2024-01-15"


@pytest.mark.unit
def test_format_date_series_mixed_column() -> None:
    series = pd.Series(["15-Jan-2024", "2024-02-01", "03/03/2024"])
    out = format_date_series(series)
    assert out.tolist() == ["2024-01-15", "2024-02-01", "2024-03-03"]


@pytest.mark.unit
def test_format_datetime_series_utc() -> None:
    series = pd.Series(["2024-01-15 09:15:00+00:00"])
    out = format_datetime_series(series, utc=True)
    assert out.iloc[0].startswith("2024-01-15")


@pytest.mark.unit
def test_parse_date_series_dayfirst() -> None:
    series = pd.Series(["15-01-2024"])
    parsed = parse_date_series(series, dayfirst=True)
    assert parsed.dt.strftime("%Y-%m-%d").iloc[0] == "2024-01-15"
