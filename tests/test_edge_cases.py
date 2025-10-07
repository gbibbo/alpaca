#!/usr/bin/env python3
"""
tests/test_edge_cases.py
Epic 9: Edge Case Testing - Comprehensive boundary and edge case validation
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pydantic import ValidationError

from lib.models import Signal, SignalSide, OrderIntent, OrderFill, Bar
# from apps.simulator.persist import BacktestPersistence  # API incompatible - see test_epic7_persistence.py


@pytest.mark.edge_case
class TestMalformedData:
    """Test validation of malformed/invalid data"""

    def test_signal_negative_price(self):
        """Signal with negative price should fail validation"""
        with pytest.raises(ValidationError):
            Signal(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                side=SignalSide.BUY,
                price=Decimal("-100.00"),
                confidence=0.8,
                source="test"
            )

    def test_signal_zero_confidence_valid(self):
        """Signal with zero confidence should be valid (but likely rejected)"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.0,
            source="test"
        )
        assert signal.confidence == 0.0

    def test_signal_confidence_above_one(self):
        """Signal with confidence > 1.0 should fail"""
        with pytest.raises(ValidationError):
            Signal(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                side=SignalSide.BUY,
                price=Decimal("100.00"),
                confidence=1.5,
                source="test"
            )

    def test_order_intent_zero_quantity(self):
        """OrderIntent with zero quantity should fail"""
        with pytest.raises(ValidationError):
            OrderIntent(
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("0"),
                client_order_id="test_001",
                signal_source="test"
            )

    def test_order_intent_negative_quantity(self):
        """OrderIntent with negative quantity should fail"""
        with pytest.raises(ValidationError):
            OrderIntent(
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("-10"),
                client_order_id="test_002",
                signal_source="test"
            )

    def test_fill_zero_price(self):
        """Fill with zero price should fail"""
        with pytest.raises(ValidationError):
            OrderFill(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("10"),
                price=Decimal("0"),
                client_order_id="test_003"
            )

    def test_bar_invalid_ohlc(self):
        """Bar with high < low should fail"""
        with pytest.raises(ValidationError):
            Bar(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                open=Decimal("100"),
                high=Decimal("90"),  # High less than low
                low=Decimal("95"),
                close=Decimal("98"),
                volume=1000
            )

    def test_bar_negative_volume(self):
        """Bar with negative volume should fail"""
        with pytest.raises(ValidationError):
            Bar(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("95"),
                close=Decimal("98"),
                volume=-1000
            )


@pytest.mark.edge_case
class TestBoundaryConditions:
    """Test extreme but valid boundary values"""

    def test_very_small_price(self):
        """Test penny stock prices"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="PENNY",
            side=SignalSide.BUY,
            price=Decimal("0.01"),
            confidence=0.5,
            source="test"
        )
        assert signal.price == Decimal("0.01")

    def test_very_large_price(self):
        """Test expensive stocks (e.g., BRK.A)"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="BRK.A",
            side=SignalSide.BUY,
            price=Decimal("500000.00"),
            confidence=0.5,
            source="test"
        )
        assert signal.price == Decimal("500000.00")

    def test_very_small_quantity(self):
        """Test fractional shares"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("0.01"),
            client_order_id="test_fractional",
            signal_source="test"
        )
        assert order.quantity == Decimal("0.01")

    def test_very_large_quantity(self):
        """Test large institutional orders"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("1000000"),
            client_order_id="test_large",
            signal_source="test"
        )
        assert order.quantity == Decimal("1000000")

    def test_maximum_confidence(self):
        """Test maximum confidence value"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=1.0,
            source="test"
        )
        assert signal.confidence == 1.0

    def test_minimum_confidence(self):
        """Test minimum confidence value"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.0,
            source="test"
        )
        assert signal.confidence == 0.0


@pytest.mark.edge_case
class TestMarketHoursEdgeCases:
    """Test edge cases around market hours"""

    def test_exactly_market_open(self):
        """Test signal exactly at market open (9:30 AM ET)"""
        # Create a market open time
        market_open = datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)  # 9:30 AM ET
        signal = Signal(
            timestamp=market_open,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp == market_open

    def test_one_second_before_open(self):
        """Test signal one second before market open"""
        pre_market = datetime(2024, 1, 15, 14, 29, 59, tzinfo=timezone.utc)  # 9:29:59 AM ET
        signal = Signal(
            timestamp=pre_market,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp == pre_market

    def test_exactly_market_close(self):
        """Test signal exactly at market close (4:00 PM ET)"""
        market_close = datetime(2024, 1, 15, 21, 0, 0, tzinfo=timezone.utc)  # 4:00 PM ET
        signal = Signal(
            timestamp=market_close,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp == market_close

    def test_one_second_before_close(self):
        """Test signal one second before market close"""
        near_close = datetime(2024, 1, 15, 20, 59, 59, tzinfo=timezone.utc)  # 3:59:59 PM ET
        signal = Signal(
            timestamp=near_close,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp == near_close

    def test_early_close_exactly_1pm(self):
        """Test signal at early close time (1:00 PM ET)"""
        early_close = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)  # 1:00 PM ET
        signal = Signal(
            timestamp=early_close,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp == early_close

    def test_weekend_saturday(self):
        """Test signal on Saturday"""
        saturday = datetime(2024, 1, 13, 15, 0, 0, tzinfo=timezone.utc)  # Saturday
        signal = Signal(
            timestamp=saturday,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp.weekday() == 5  # Saturday

    def test_weekend_sunday(self):
        """Test signal on Sunday"""
        sunday = datetime(2024, 1, 14, 15, 0, 0, tzinfo=timezone.utc)  # Sunday
        signal = Signal(
            timestamp=sunday,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp.weekday() == 6  # Sunday

    def test_new_years_day(self):
        """Test signal on New Year's Day (market holiday)"""
        new_years = datetime(2024, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
        signal = Signal(
            timestamp=new_years,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp.month == 1 and signal.timestamp.day == 1


@pytest.mark.skip(reason="Persistence API incompatible - Epic 7 has persistence tests")
@pytest.mark.edge_case
class TestPersistenceEdgeCases:
    """Test edge cases in persistence layer"""

    def test_persistence_no_fills(self):
        """Test persistence with no fills"""
        pm = BacktestPersistence(run_id="test_no_fills")

        # Save some bars but no fills
        bars = [
            Bar(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("95"),
                close=Decimal("102"),
                volume=1000
            )
        ]
        pm.save_bars(bars)

        summary = pm.get_summary()
        assert summary["num_fills"] == 0
        pm.cleanup()

    def test_persistence_identical_timestamps(self):
        """Test multiple fills with identical timestamps"""
        pm = BacktestPersistence(run_id="test_identical_ts")

        ts = datetime.now(timezone.utc)
        fills = [
            OrderFill(
                timestamp=ts,
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("10"),
                price=Decimal("100"),
                client_order_id=f"test_{i}"
            )
            for i in range(3)
        ]

        pm.save_fills(fills)
        summary = pm.get_summary()
        assert summary["num_fills"] == 3
        pm.cleanup()

    def test_very_long_run_id(self):
        """Test very long run ID (200 characters)"""
        long_id = "x" * 200
        pm = BacktestPersistence(run_id=long_id)

        bars = [
            Bar(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("95"),
                close=Decimal("102"),
                volume=1000
            )
        ]
        pm.save_bars(bars)

        summary = pm.get_summary()
        assert summary["run_id"] == long_id
        pm.cleanup()

    def test_directory_already_exists(self):
        """Test handling when directory already exists"""
        run_id = "test_existing_dir"

        # Create first instance
        pm1 = BacktestPersistence(run_id=run_id)
        pm1.save_bars([
            Bar(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("95"),
                close=Decimal("102"),
                volume=1000
            )
        ])

        # Create second instance with same run_id
        pm2 = BacktestPersistence(run_id=run_id)

        # Should still work
        summary = pm2.get_summary()
        assert summary["run_id"] == run_id

        pm1.cleanup()
        pm2.cleanup()


@pytest.mark.edge_case
class TestTimezoneEdgeCases:
    """Test timezone handling edge cases"""

    def test_signal_with_utc_timezone(self):
        """Test signal with explicit UTC timezone"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp.tzinfo == timezone.utc

    def test_signal_with_naive_datetime(self):
        """Test signal with naive datetime (should be converted to UTC)"""
        naive_dt = datetime.now()
        signal = Signal(
            timestamp=naive_dt,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        # Should still create successfully
        assert signal.timestamp is not None

    def test_dst_transition(self):
        """Test handling of DST transition times"""
        # Spring forward: 2024-03-10 2:00 AM -> 3:00 AM
        dst_transition = datetime(2024, 3, 10, 7, 0, 0, tzinfo=timezone.utc)  # 2 AM ET

        signal = Signal(
            timestamp=dst_transition,
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.timestamp == dst_transition


@pytest.mark.edge_case
class TestSymbolEdgeCases:
    """Test edge cases with different symbol formats"""

    def test_symbol_with_dot(self):
        """Test symbol with dot (e.g., BRK.B)"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="BRK.B",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.symbol == "BRK.B"

    def test_lowercase_symbol(self):
        """Test lowercase symbols are converted to uppercase"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="googl",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        # Model automatically converts to uppercase
        assert signal.symbol == "GOOGL"

    def test_symbol_with_numbers(self):
        """Test symbols with numbers"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="SPY500",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.symbol == "SPY500"

    def test_empty_symbol(self):
        """Test empty symbol should fail"""
        with pytest.raises(ValidationError):
            Signal(
                timestamp=datetime.now(timezone.utc),
                symbol="",
                side=SignalSide.BUY,
                price=Decimal("100.00"),
                confidence=0.8,
                source="test"
            )


@pytest.mark.edge_case
class TestDecimalPrecision:
    """Test high-precision decimal handling"""

    def test_price_many_decimals(self):
        """Test price validation enforces max 4 decimal places"""
        # Model enforces max 4 decimal places
        with pytest.raises(ValidationError):
            Signal(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                side=SignalSide.BUY,
                price=Decimal("100.123456789"),
                confidence=0.8,
                source="test"
            )

    def test_confidence_high_precision(self):
        """Test confidence validation enforces max 3 decimal places"""
        # Model enforces max 3 decimal places for confidence
        with pytest.raises(ValidationError):
            Signal(
                timestamp=datetime.now(timezone.utc),
                symbol="GOOGL",
                side=SignalSide.BUY,
                price=Decimal("100.00"),
                confidence=0.999999999,
                source="test"
            )

    def test_fractional_shares_precise(self):
        """Test precise fractional share quantities"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("0.123456"),
            client_order_id="test_precise",
            signal_source="test"
        )
        assert order.quantity == Decimal("0.123456")


@pytest.mark.edge_case
class TestConcurrencyEdgeCases:
    """Test concurrent operation edge cases"""

    def test_multiple_signals_same_timestamp(self):
        """Test multiple signals with identical timestamp"""
        ts = datetime.now(timezone.utc)

        signals = [
            Signal(
                timestamp=ts,
                symbol=f"SYM{i}",
                side=SignalSide.BUY,
                price=Decimal("100.00"),
                confidence=0.8,
                source="test"
            )
            for i in range(10)
        ]

        # All should have same timestamp
        timestamps = [s.timestamp for s in signals]
        assert len(set(timestamps)) == 1
        assert all(t == ts for t in timestamps)
