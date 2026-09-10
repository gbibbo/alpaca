#!/usr/bin/env python3
"""tests/test_settings_validation.py — risk settings are validated fail-fast at construction."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
from lib.settings import Settings


def test_defaults_are_valid():
    s = Settings(_env_file=None)
    assert 0 < s.max_position_size <= 1


@pytest.mark.parametrize("field,bad", [
    ("max_daily_loss", 0), ("max_daily_loss", 1.5), ("max_position_size", -0.1),
    ("stop_loss_pct", 2.0), ("take_profit_pct", 0), ("risk_pct", 1.5),
])
def test_out_of_range_rejected(field, bad):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: bad})


def test_bad_orders_per_minute_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, max_orders_per_minute=0)
