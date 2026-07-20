"""Regression tests for safe parquet frame concatenation."""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pandas as pd
import pytest

from trade_integrations.hub_storage.parquet_io import (
    concat_dataframes,
    concat_frames,
    upsert_by_keys,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INTEGRATIONS_ROOT = _REPO_ROOT / "integrations" / "trade_integrations"
_AXIS1_ALLOWLIST = {
    _INTEGRATIONS_ROOT / "dataflows/index_research/alpha_bridge/panel.py",
    _INTEGRATIONS_ROOT / "dataflows/index_research/alpha_bridge/compute.py",
    _INTEGRATIONS_ROOT / "dataflows/index_research/technical_features.py",
}
_FORBIDDEN_ROW_CONCAT = re.compile(
    r"pd\.concat\s*\(\s*\[.*\]\s*,\s*ignore_index\s*=\s*True\s*\)|"
    r"pd\.concat\s*\(\s*[^,\n]+\s*,\s*ignore_index\s*=\s*True\s*\)"
)


def _heterogeneous_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    existing = pd.DataFrame(
        [
            {
                "entity_id": "NIFTY",
                "strike": 24000,
                "option_type": "CE",
                "ltp": 100,
                "oi": 500,
                "series": "derivatives_chain",
            },
            {
                "entity_id": "NIFTY",
                "strike": 24000,
                "option_type": "PE",
                "ltp": 80,
                "oi": 400,
                "series": "derivatives_chain",
            },
        ]
    )
    incoming = pd.DataFrame(
        [
            {
                "entity_id": "NIFTY",
                "series": "pcr_summary",
                "nifty_pcr": 1.2,
                "leg_count": 2,
                "source": "openalgo",
            }
        ]
    )
    return existing, incoming


@pytest.mark.parametrize(
    ("merge_fn", "expected_len"),
    [
        (lambda left, right: concat_dataframes(left, right), 3),
        (lambda left, right: concat_frames([left, right]), 3),
        (lambda left, right: upsert_by_keys(left, right, dedupe_keys=["series"]), 2),
    ],
)
def test_heterogeneous_append_no_future_warning(merge_fn, expected_len):
    existing, incoming = _heterogeneous_frames()
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        merged = merge_fn(existing, incoming)
    assert len(merged) == expected_len
    assert "nifty_pcr" in merged.columns
    assert "strike" in merged.columns


def test_concat_frames_skips_empty_inputs():
    left = pd.DataFrame([{"a": 1}])
    merged = concat_frames([pd.DataFrame(), left, pd.DataFrame()])
    assert len(merged) == 1


def test_no_row_oriented_pd_concat_in_trade_integrations():
    offenders: list[str] = []
    for path in _INTEGRATIONS_ROOT.rglob("*.py"):
        if path in _AXIS1_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        if "axis=1" in text and "pd.concat" in text and path in _AXIS1_ALLOWLIST:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "pd.concat" not in line:
                continue
            if "axis=1" in line:
                continue
            if "ignore_index=True" in line or (
                "ignore_index=True" not in line and re.search(r"pd\.concat\s*\(\s*[^,\n]+\s*,\s*ignore_index", line)
            ):
                if _FORBIDDEN_ROW_CONCAT.search(line):
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}:{line_no}: {line.strip()}")
    assert not offenders, "row-oriented pd.concat remains:\n" + "\n".join(offenders)
