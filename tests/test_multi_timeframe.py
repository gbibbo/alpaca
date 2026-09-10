#!/usr/bin/env python3
"""
tests/test_multi_timeframe.py
Multi-timeframe plumbing: timeframe parsing, 1m -> 5m/1h resampling, strategy routing by
(symbol, timeframe), simulator labelling, and portfolio-aware position sizing.
"""

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("USE_FAKE_REDIS", "1")

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

import pytest

from lib.models import Bar, Signal, SignalSide, TimeFrame
from lib.timeframes import parse_timeframe, bucket_start, timeframe_minutes
from lib.resampler import BarResampler
from lib.strategy_base import Strategy, STRATEGY_REGISTRY, create_strategies, registered_names


def make_bars(symbol="AAPL", n=20, start=None, tf=TimeFrame.MINUTE, price=100.0, step=0.1) -> List[Bar]:
    start = start or datetime(2026, 9, 9, 13, 30, tzinfo=timezone.utc)
    minutes = timeframe_minutes(tf)
    bars = []
    for i in range(n):
        p = price + i * step
        bars.append(Bar(symbol=symbol, timestamp=start + timedelta(minutes=i * minutes),
                        open=p, high=p + 0.05, low=p - 0.05, close=p + 0.02, volume=10, timeframe=tf))
    return bars


class TestTimeframes:
    def test_parse_aliases(self):
        assert parse_timeframe("1Min") == TimeFrame.MINUTE
        assert parse_timeframe("5m") == TimeFrame.FIVE_MINUTE
        assert parse_timeframe("1Hour") == TimeFrame.HOUR
        assert parse_timeframe("1d") == TimeFrame.DAY
        assert parse_timeframe(TimeFrame.HOUR) == TimeFrame.HOUR
        with pytest.raises(ValueError):
            parse_timeframe("2Weeks")

    def test_bucket_start(self):
        ts = datetime(2026, 9, 9, 13, 37, 42, tzinfo=timezone.utc)
        assert bucket_start(ts, TimeFrame.FIVE_MINUTE) == datetime(2026, 9, 9, 13, 35, tzinfo=timezone.utc)
        assert bucket_start(ts, TimeFrame.HOUR) == datetime(2026, 9, 9, 13, 0, tzinfo=timezone.utc)
        assert bucket_start(ts, TimeFrame.DAY) == datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)
        naive = datetime(2026, 9, 9, 13, 37)
        assert bucket_start(naive, TimeFrame.FIVE_MINUTE).tzinfo is not None


class TestResampler:
    def test_five_minute_aggregation(self):
        rs = BarResampler([TimeFrame.FIVE_MINUTE])
        bars = make_bars(n=11, start=datetime(2026, 9, 9, 13, 30, tzinfo=timezone.utc))
        out = []
        for b in bars:
            out.extend(rs.add(b))
        # 11 minutes 13:30..13:40 -> buckets 13:30, 13:35 complete, 13:40 open
        assert [b.timestamp.minute for b in out] == [30, 35]
        first = out[0]
        assert first.timeframe == TimeFrame.FIVE_MINUTE
        assert first.open == bars[0].open
        assert first.close == bars[4].close
        assert first.high == max(b.high for b in bars[:5])
        assert first.low == min(b.low for b in bars[:5])
        assert first.volume == sum(b.volume for b in bars[:5])
        assert rs.flush() and rs.get_stats()["open_buckets"] == 0

    def test_hour_and_five_minute_together(self):
        rs = BarResampler(["5m", "1h"])
        bars = make_bars(n=61, start=datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc))
        out = []
        for b in bars:
            out.extend(rs.add(b))
        fives = [b for b in out if b.timeframe == TimeFrame.FIVE_MINUTE]
        hours = [b for b in out if b.timeframe == TimeFrame.HOUR]
        assert len(fives) == 12
        assert len(hours) == 1 and hours[0].timestamp == datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
        assert hours[0].volume == sum(b.volume for b in bars[:60])

    def test_ignores_duplicates_and_non_minute_input(self):
        rs = BarResampler([TimeFrame.FIVE_MINUTE])
        bars = make_bars(n=3)
        for b in bars:
            rs.add(b)
        assert rs.add(bars[-1]) == []            # duplicate minute
        assert rs.dropped_out_of_order == 1
        daily = make_bars(n=1, tf=TimeFrame.DAY)[0]
        assert rs.add(daily) == []               # daily input is never resampled
        assert rs.bars_in == 3


class TestStrategyRouting:
    def test_registry_has_one_strategy_per_horizon(self):
        names = registered_names()
        by_tf = {STRATEGY_REGISTRY[n].timeframe for n in names}
        assert {TimeFrame.MINUTE, TimeFrame.FIVE_MINUTE, TimeFrame.HOUR, TimeFrame.DAY} <= by_tf
        with pytest.raises(ValueError):
            create_strategies(["does_not_exist"])

    def test_engine_keeps_histories_per_timeframe(self):
        from apps.strategies.main import StrategyEngine

        published: List[Signal] = []

        class Spy5m(Strategy):
            name = "spy_5m_test"
            timeframe = TimeFrame.FIVE_MINUTE
            lookback_bars = 3
            cooldown_seconds = 0

            def analyze(self, symbol, bars):
                assert all(b.timeframe == TimeFrame.FIVE_MINUTE for b in bars)
                return self.make_signal(symbol, SignalSide.BUY, 0.7, bars)

        class Spy1m(Strategy):
            name = "spy_1m_test"
            timeframe = TimeFrame.MINUTE
            lookback_bars = 3
            cooldown_seconds = 0

            def analyze(self, symbol, bars):
                assert all(b.timeframe == TimeFrame.MINUTE for b in bars)
                return None

        os.environ["STRATEGIES_METRICS_PORT"] = "0"
        engine = StrategyEngine(strategies=[Spy5m(), Spy1m()])
        engine.bus.publish_signal = lambda s: published.append(s)
        engine.bus.publish_system_event = lambda **kw: None

        minute_bars = make_bars(n=6, tf=TimeFrame.MINUTE)
        five_bars = make_bars(n=4, tf=TimeFrame.FIVE_MINUTE)
        for b in minute_bars[:3] + five_bars[:2] + minute_bars[3:] + five_bars[2:]:
            engine.process_bar(b)

        assert len(engine.bar_history[("AAPL", TimeFrame.MINUTE)]) == 6
        assert len(engine.bar_history[("AAPL", TimeFrame.FIVE_MINUTE)]) == 4
        # Spy5m fires once lookback (3) is reached: on the 3rd and 4th 5m bar
        assert [s.source for s in published] == ["spy_5m_test", "spy_5m_test"]
        assert published[0].timeframe == TimeFrame.FIVE_MINUTE
        assert published[0].expire_seconds == Spy5m().signal_expiry_seconds

    def test_cooldown_uses_bar_time(self):
        from apps.strategies.main import StrategyEngine

        class Always(Strategy):
            name = "always_test"
            timeframe = TimeFrame.MINUTE
            lookback_bars = 1
            cooldown_seconds = 300

            def analyze(self, symbol, bars):
                return self.make_signal(symbol, SignalSide.BUY, 0.9, bars)

        os.environ["STRATEGIES_METRICS_PORT"] = "0"
        engine = StrategyEngine(strategies=[Always()])
        out = []
        engine.bus.publish_signal = lambda s: out.append(s)
        engine.bus.publish_system_event = lambda **kw: None
        for b in make_bars(n=11):  # 11 minutes -> signals at minute 0, 5, 10
            engine.process_bar(b)
        assert len(out) == 3


class TestSimulatorLabels:
    def test_alpaca_loader_labels_hour_bars_as_hour(self):
        from apps.simulator.main import AlpacaDataLoader
        assert parse_timeframe("1Hour") == TimeFrame.HOUR  # what the loader now uses
        loader = AlpacaDataLoader.__new__(AlpacaDataLoader)  # no network
        import csv, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            w.writerow(["2026-09-09T14:00:00Z", 100, 101, 99, 100.5, 1000])
            path = f.name
        bars = loader.load_from_csv(path, "AAPL", "1Hour")
        assert bars and bars[0].timeframe == TimeFrame.HOUR


class TestPortfolioAwareSizing:
    @pytest.fixture
    def rm(self, monkeypatch):
        os.environ["RISK_METRICS_PORT"] = "0"
        from apps.risk_manager.main import EnhancedRiskManager
        monkeypatch.setattr(EnhancedRiskManager, "__init__", lambda self: None)
        from lib.settings import get_settings
        m = EnhancedRiskManager()
        m.settings = get_settings()
        m.alpaca_client = None
        m._positions_cache = {}
        m._positions_cached_at = 0.0
        m._portfolio_value_cache = None
        m._portfolio_value_cached_at = 0.0
        return m

    def test_buy_capped_by_existing_exposure(self, rm):
        equity = Decimal("100000")
        sig = Signal(symbol="NVDA", side=SignalSide.BUY, confidence=1.0, price=Decimal("100"), source="hourly_trend")
        # max_position_size default 10% -> $10k; already holding $8.5k -> room $1.5k -> 15 shares
        held = {"NVDA": {"qty": Decimal("85"), "market_value": Decimal("8500")}}
        qty, why = rm.calculate_position_size(sig, equity, held)
        assert qty == Decimal("15"), why
        # at the cap -> no trade
        held = {"NVDA": {"qty": Decimal("100"), "market_value": Decimal("10000")}}
        qty, why = rm.calculate_position_size(sig, equity, held)
        assert qty == 0 and "limit" in why.lower()

    def test_buy_without_position_uses_risk_budget(self, rm):
        equity = Decimal("100000")
        sig = Signal(symbol="AAPL", side=SignalSide.BUY, confidence=0.5, price=Decimal("200"), source="hourly_trend")
        # risk_pct default 2% -> $2000 * 0.5 confidence = $1000 -> 5 shares
        qty, why = rm.calculate_position_size(sig, equity, {})
        assert qty == Decimal("5"), why

    def test_sell_limited_to_holdings(self, rm):
        equity = Decimal("100000")
        sig = Signal(symbol="AAPL", side=SignalSide.SELL, confidence=0.9, price=Decimal("200"), source="hourly_trend")
        qty, why = rm.calculate_position_size(sig, equity, {})
        assert qty == 0 and "no long position" in why.lower()
        qty, why = rm.calculate_position_size(sig, equity, {"AAPL": {"qty": Decimal("7"), "market_value": Decimal("1400")}})
        assert qty == Decimal("7")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
