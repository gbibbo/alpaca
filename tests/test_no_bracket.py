#!/usr/bin/env python3
"""
tests/test_no_bracket.py
Optional protective exits on the LIVE path: require_protective_exits (default True) is enforced
by execution_safety and the executor; when disabled, a BUY without stop/take-profit submits a
plain (non-bracket) order for pure trend/momentum strategies.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from lib.models import OrderIntent, SignalSide, OrderType


def _safe_client():
    client = Mock()
    client.get_account = Mock(return_value=Mock(equity="100000", cash="100000",
                                                buying_power="100000", last_equity="100000"))
    client.get_all_positions = Mock(return_value=[])
    client.get_orders = Mock(return_value=[])
    return client


def _redis():
    r = Mock()
    r.get = Mock(return_value=None)   # no emergency stop
    return r


def _buy_intent(**kw):
    base = dict(symbol="TEST", side=SignalSide.BUY, quantity=Decimal("1"), order_type=OrderType.MARKET,
                client_order_id="nb", signal_source="t", price=Decimal("100"))
    base.update(kw)
    return OrderIntent(**base)


class TestExecutionSafetyGate:
    def test_requires_exits_by_default(self):
        from lib.execution_safety import check_order
        from lib.settings import Settings
        s = Settings(_env_file=None, trading_mode="paper", require_protective_exits=True)
        with pytest.raises(RuntimeError, match="protective exits"):
            check_order(_safe_client(), _redis(), s, _buy_intent())  # no stop/tp

    def test_allows_no_exits_when_disabled(self):
        from lib.execution_safety import check_order
        from lib.settings import Settings
        s = Settings(_env_file=None, trading_mode="paper", require_protective_exits=False)
        check_order(_safe_client(), _redis(), s, _buy_intent())      # no raise (ample cash)


class TestExecutorRequestShape:
    def _executor(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EXECUTOR_JOURNAL", str(tmp_path / "j.sqlite"))
        monkeypatch.setenv("EXECUTOR_METRICS_PORT", "0")
        from apps.executor.main import EnhancedAlpacaExecutor
        client = _safe_client()
        client.get_order_by_client_id = Mock(side_effect=Exception("order not found"))
        resp = Mock(id="b1", status="filled", symbol="TEST", qty=1, side="buy", legs=None,
                    filled_qty=1, filled_avg_price=100, limit_price=100)
        captured = {}
        def _submit(req):
            captured["req"] = req
            return resp
        client.submit_order = Mock(side_effect=_submit)
        client.get_order_by_id = Mock(return_value=resp)
        with patch("apps.executor.main.TradingClient", return_value=client):
            ex = EnhancedAlpacaExecutor()
            ex.trading_client = client
        monkeypatch.setattr(ex.settings, "trading_mode", "paper")
        monkeypatch.setattr("lib.execution_safety.check_order", lambda *a, **k: None)
        return ex, client, captured

    @pytest.mark.asyncio
    async def test_bracketfree_buy_is_plain(self, monkeypatch, tmp_path):
        ex, client, captured = self._executor(monkeypatch, tmp_path)
        monkeypatch.setattr(ex.settings, "require_protective_exits", False)
        fill = await ex.execute_order_with_validation(_buy_intent())   # no stop/tp
        assert client.submit_order.called
        req = captured["req"]
        assert "BRACKET" not in str(getattr(req, "order_class", "")).upper()

    @pytest.mark.asyncio
    async def test_default_requires_exits(self, monkeypatch, tmp_path):
        ex, client, captured = self._executor(monkeypatch, tmp_path)
        monkeypatch.setattr(ex.settings, "require_protective_exits", True)
        with pytest.raises(Exception):
            await ex.execute_order_with_validation(_buy_intent())      # no stop/tp -> refused
        assert not client.submit_order.called

    @pytest.mark.asyncio
    async def test_bracket_still_used_when_exits_present(self, monkeypatch, tmp_path):
        ex, client, captured = self._executor(monkeypatch, tmp_path)
        monkeypatch.setattr(ex.settings, "require_protective_exits", True)
        await ex.execute_order_with_validation(_buy_intent(stop_loss=Decimal("95"), take_profit=Decimal("110")))
        assert "BRACKET" in str(getattr(captured["req"], "order_class", "")).upper()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
