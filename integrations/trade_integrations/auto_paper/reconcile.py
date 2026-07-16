"""Pre-turn reconcile: OpenAlgo broker truth vs local execution ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient
from trade_integrations.monitor.execution_ledger import (
    list_open_entries,
    match_positions_for_entry,
)


@dataclass
class PaperReconcileReport:
    is_safe: bool = True
    requires_halt: bool = False
    open_ledger_count: int = 0
    broker_position_count: int = 0
    matched_positions: int = 0
    orphan_broker_positions: list[dict[str, Any]] = field(default_factory=list)
    orphan_ledger_entries: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def reconcile_paper_state() -> PaperReconcileReport:
    """Compare ledger open entries to OpenAlgo sandbox position book."""
    report = PaperReconcileReport()
    open_entries = list_open_entries()
    report.open_ledger_count = len(open_entries)

    try:
        client = OpenAlgoClient()
        broker_rows = client.get_position_book()
    except RuntimeError as exc:
        report.is_safe = False
        report.messages.append(f"broker_unavailable: {exc}")
        return report

    report.broker_position_count = len(broker_rows)
    broker_symbols = {
        str(row.get("symbol", "")).upper()
        for row in broker_rows
        if row.get("quantity") not in (0, "0", None)
    }

    ledger_symbols: set[str] = set()
    for entry in open_entries:
        legs = entry.get("legs") or []
        leg_syms = {
            str(leg.get("symbol", "")).upper()
            for leg in legs
            if isinstance(leg, dict) and leg.get("symbol")
        }
        ledger_symbols.update(leg_syms)
        matched, _ = match_positions_for_entry(entry, {"data": broker_rows})
        if matched:
            report.matched_positions += len(matched)
        else:
            report.orphan_ledger_entries.append(
                {"widget_id": entry.get("widget_id"), "underlying": entry.get("underlying")}
            )
            report.messages.append(
                f"ledger entry {entry.get('widget_id')} has no matching broker positions"
            )

    for row in broker_rows:
        sym = str(row.get("symbol", "")).upper()
        qty = row.get("quantity")
        if sym and sym not in ledger_symbols and qty not in (0, "0", None):
            report.orphan_broker_positions.append(row)
            report.messages.append(f"broker position {sym} not in ledger")

    if report.orphan_ledger_entries and report.open_ledger_count > 0:
        report.requires_halt = False
    if len(report.orphan_broker_positions) > 3:
        report.requires_halt = True
        report.is_safe = False
        report.messages.append("too many orphan broker positions — halt for manual review")

    return report
