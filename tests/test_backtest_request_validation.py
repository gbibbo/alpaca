#!/usr/bin/env python3
"""
tests/test_backtest_request_validation.py
API BacktestRequest validation: dotted tickers (BRK.B) are accepted, traversal is rejected, and
a point-in-time universe_csv must stay inside data/.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("TRADING_MODE", "backtest")
os.environ.setdefault("USE_FAKE_REDIS", "1")

import pytest


def _req(**kw):
    from apps.api.main import BacktestRequest
    base = dict(symbols=["AAPL"], start_date="2022-01-01", end_date="2022-06-01",
                timeframe="1Day", strategies=["daily_trend"], csv_dir="data/real")
    base.update(kw)
    return BacktestRequest(**base)


def test_accepts_dotted_ticker():
    r = _req(symbols=["BRK.B", "BF.B"])
    assert r.symbols == ["BRK.B", "BF.B"]


def test_rejects_traversal_symbol():
    with pytest.raises(Exception):
        _req(symbols=["../secret"])
    with pytest.raises(Exception):
        _req(symbols=["a/b"])


def test_universe_csv_must_be_inside_data():
    with pytest.raises(Exception):
        _req(risk_params={"universe_csv": "C:/Windows/System32/x.csv"})
    with pytest.raises(Exception):
        _req(risk_params={"universe_csv": "../../etc/passwd"})
    # inside data/ is accepted
    r = _req(risk_params={"universe_csv": "data/real/composition.csv"})
    assert r.risk_params["universe_csv"].endswith("composition.csv")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
