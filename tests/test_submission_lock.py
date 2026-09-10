#!/usr/bin/env python3
"""
tests/test_submission_lock.py
The cross-executor submission lock: only one executor validates/submits at a time, a lost lease
is caught before submitting, and a failed acquisition never clobbers the current holder's lock.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from decimal import Decimal
import pytest
from unittest.mock import Mock, patch

from lib.models import OrderIntent, SignalSide, OrderType

pytestmark = pytest.mark.asyncio


def _executor(monkeypatch, tmp_path):
    monkeypatch.setenv("EXECUTOR_JOURNAL", str(tmp_path / "j.sqlite"))
    monkeypatch.setenv("EXECUTOR_METRICS_PORT", "0")
    from apps.executor.main import EnhancedAlpacaExecutor
    client = Mock()
    client.get_account = Mock(return_value=Mock(status="ACTIVE", buying_power=100000,
                                                cash=100000, portfolio_value=100000, last_equity=100000))
    client.submit_order = Mock()
    with patch("apps.executor.main.TradingClient", return_value=client):
        ex = EnhancedAlpacaExecutor()
        ex.trading_client = client
    return ex, client


def _intent():
    return OrderIntent(symbol="TEST", side=SignalSide.BUY, quantity=Decimal("1"),
                       order_type=OrderType.MARKET, client_order_id="lk", signal_source="t",
                       price=Decimal("100"), stop_loss=Decimal("95"), take_profit=Decimal("110"))


async def test_second_executor_is_blocked(monkeypatch, tmp_path):
    ex, client = _executor(monkeypatch, tmp_path)
    monkeypatch.setattr(ex.settings, "trading_mode", "paper")
    # Someone else already holds the lock.
    ex.bus.redis_client.set("trading:submission_lock", "other-owner", nx=True, ex=120)
    with pytest.raises(RuntimeError, match="Another executor"):
        await ex.execute_order_with_validation(_intent())
    # The other owner's lock is left intact and submit was never attempted.
    assert ex.bus.redis_client.get("trading:submission_lock") == "other-owner"
    assert client.submit_order.call_count == 0


def test_lost_lease_blocks_submit(monkeypatch, tmp_path):
    ex, client = _executor(monkeypatch, tmp_path)
    # We think we hold token A, but the lock is now owned by B (our lease expired and was retaken).
    ex._submission_token = "A"
    ex.bus.redis_client.set("trading:submission_lock", "B")
    with pytest.raises(RuntimeError, match="lease expired"):
        ex._submit_guarded(_intent(), object())
    assert client.submit_order.call_count == 0


def test_emergency_stop_blocks_guarded_submit(monkeypatch, tmp_path):
    ex, client = _executor(monkeypatch, tmp_path)
    ex._submission_token = "A"
    ex.bus.redis_client.set("trading:submission_lock", "A")
    ex.bus.redis_client.set("trading:emergency_stop", "stop-1")
    monkeypatch.setattr("lib.execution_safety.check_order", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="Emergency stop"):
        ex._submit_guarded(_intent(), object())
    assert client.submit_order.call_count == 0
