"""OpenAlgo adapter symbol mapping and vendor registration."""

import copy
import unittest

import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
import trade_integrations  # noqa: F401 — apply runtime patches
from trade_integrations.dataflows.openalgo import resolve_openalgo_symbol
from trade_integrations.dataflows.errors import NoMarketDataError


def _reset_config():
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


@pytest.mark.unit
class OpenAlgoSymbolTests(unittest.TestCase):
    def test_nse_suffix(self):
        self.assertEqual(resolve_openalgo_symbol("RELIANCE.NS"), ("RELIANCE", "NSE"))

    def test_bse_suffix(self):
        self.assertEqual(resolve_openalgo_symbol("RELIANCE.BO"), ("RELIANCE", "BSE"))

    def test_index_alias(self):
        self.assertEqual(resolve_openalgo_symbol("^NSEI"), ("NIFTY", "NSE_INDEX"))

    def test_plain_equity_defaults_nse(self):
        self.assertEqual(resolve_openalgo_symbol("SBIN"), ("SBIN", "NSE"))

    def test_unmapped_index_raises(self):
        with self.assertRaises(NoMarketDataError):
            resolve_openalgo_symbol("^GSPC")


@pytest.mark.unit
class OpenAlgoVendorRegistrationTests(unittest.TestCase):
    def setUp(self):
        _reset_config()

    def tearDown(self):
        _reset_config()

    def test_openalgo_registered_for_stock_and_indicators(self):
        import trade_integrations  # noqa: F401
        from tradingagents.dataflows import interface

        self.assertIn("openalgo", interface.VENDOR_METHODS["get_stock_data"])
        self.assertIn("openalgo", interface.VENDOR_METHODS["get_indicators"])
        self.assertIn("openalgo", interface.VENDOR_LIST)
