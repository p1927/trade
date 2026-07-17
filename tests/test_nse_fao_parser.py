"""Regression tests for NSE F&O participant OI CSV parsing."""

from __future__ import annotations


def test_parse_fao_participant_csv_skips_title_row():
    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        _parse_fao_participant_csv,
    )

    sample = '''""Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Jul 16, 2026"",,,,,,,,,,,,,,
Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short       ,Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,Total Long Contracts      ,Total Short Contracts
Client,228686,59851,3179481,296958,2705275,2103936,2620666,2749094,2435365,832253,1341188,1168589,11484996,8236346
DII,74619,10920,374634,4264717,7715,32404,0,214,4691,36141,411856,13788,530204,4701495
FII,25419,275305,3784692,3250525,484524,915636,703407,427465,263460,394183,423850,253329,5867913,5333881
'''
    parsed = _parse_fao_participant_csv(sample)
    assert "FII" in parsed
    assert "DII" in parsed
    assert parsed["FII"]["fii_idx_fut_long"] == 25419.0
    assert parsed["FII"]["fii_idx_put_oi"] == 427465.0
    assert parsed["FII"]["fii_idx_call_oi"] == 703407.0
