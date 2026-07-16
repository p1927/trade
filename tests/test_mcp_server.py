"""Smoke tests for OpenAlgo MCP server module."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MCP = ROOT / "openalgo" / "mcp" / "mcpserver.py"


@pytest.mark.unit
def test_mcpserver_py_compile():
    if not MCP.is_file():
        pytest.skip("openalgo submodule not checked out")
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(MCP)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
