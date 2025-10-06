#!/usr/bin/env python3
"""
scripts/test_system_health.py
Comprehensive system health check for Epic 6 & 7 integration
Tests all components and their interactions
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
from datetime import datetime, timedelta
from decimal import Decimal
import uuid

# Test counters
total_tests = 0
passed_tests = 0
failed_tests = 0

def run_test(test_name, test_func):
    """Run a single test and track results"""
    global total_tests, passed_tests, failed_tests

    total_tests += 1
    print(f"\n[Test {total_tests}] {test_name}")
    print("-" * 60)

    try:
        test_func()
        print("✅ PASS")
        passed_tests += 1
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        failed_tests += 1
        import traceback
        traceback.print_exc()
        return False


def test_imports():
    """Test all critical imports"""
    print("Testing imports...")

    from lib.bus import get_bus, connect_bus
    from lib.models import Signal, Bar, OrderIntent, SignalSide, OrderType, TimeFrame
    from lib.settings import get_settings
    from lib.time_utils import TimeUtils
    from lib.deduplication import get_deduplication_service
    from lib.metrics_helpers import ServiceMetrics, RiskManagerMetrics

    from apps.risk_manager.main import EnhancedRiskManager, RiskManager
    from apps.risk_manager.market_hours import MarketCalendar, MarketHoursValidator
    from apps.simulator.main import HistoricalSimulator
    from apps.simulator.persist import BacktestPersistence

    print("All imports successful")


def test_risk_manager_market_hours():
    """Test Risk Manager with new market hours validator"""
    print("Testing Risk Manager with market hours validation...")

    from apps.risk_manager.main import EnhancedRiskManager

    # Initialize
    rm = EnhancedRiskManager()

    # Check that new validator is there
    assert hasattr(rm, 'market_validator'), "Missing market_validator"
    assert rm.market_validator is not None, "market_validator is None"

    # Check validator type
    from apps.risk_manager.market_hours import MarketHoursValidator
    assert isinstance(rm.market_validator, MarketHoursValidator), "Wrong validator type"

    # Test validation
    is_open, reason = rm.market_validator.validate_trading_hours()
    print(f"  Market status: {is_open} - {reason}")

    # Test stats
    stats = rm.market_validator.get_stats()
    assert "is_open" in stats
    assert "timezone" in stats
    print(f"  Timezone: {stats['timezone']}")

    print("Market hours validator working correctly")


def test_risk_manager_signal_processing():
    """Test that Risk Manager still processes signals correctly"""
    print("Testing Risk Manager signal processing...")

    from apps.risk_manager.main import EnhancedRiskManager
    from lib.models import Signal, SignalSide
    from datetime import timezone

    rm = EnhancedRiskManager()

    # Create test signal with timezone-aware datetime
    signal = Signal(
        signal_id=uuid.uuid4(),
        symbol="AAPL",
        timestamp=datetime.now(timezone.utc),  # Fixed: timezone-aware
        side=SignalSide.BUY,
        confidence=Decimal("0.85"),
        price=Decimal("150.0"),
        source="random_50_50"
    )

    # Validate (may pass or fail depending on market hours)
    is_valid, reason = rm.validate_signal_comprehensive(signal)
    print(f"  Signal validation: {is_valid} - {reason}")

    # Test that validation doesn't crash
    assert isinstance(is_valid, bool), "Validation should return bool"
    assert isinstance(reason, str), "Reason should be string"

    print("Signal processing intact")


def test_risk_manager_backward_compat():
    """Test backward compatibility with old RiskManager alias"""
    print("Testing backward compatibility...")

    from apps.risk_manager.main import RiskManager

    # Old alias should still work
    rm = RiskManager()
    assert rm is not None
    assert hasattr(rm, 'market_validator')

    print("Backward compatibility maintained")


def test_simulator_no_persistence():
    """Test Simulator WITHOUT persistence (backward compatibility)"""
    print("Testing Simulator without persistence...")

    from apps.simulator.main import HistoricalSimulator

    # Old way - no persistence
    sim = HistoricalSimulator(speed_multiplier=10.0)

    assert sim.persistence is None, "Should have no persistence"
    assert sim.speed_multiplier == 10.0
    assert sim.enable_persistence == False

    print("Simulator works without persistence (backward compat)")


def test_simulator_with_persistence():
    """Test Simulator WITH persistence (new feature)"""
    print("Testing Simulator with persistence...")

    from apps.simulator.main import HistoricalSimulator
    import tempfile
    import shutil

    # Create simulator with persistence
    sim = HistoricalSimulator(enable_persistence=True)

    assert sim.persistence is not None, "Should have persistence"
    assert sim.persistence.db_path.exists(), "Database should exist"
    assert sim.persistence.run_dir.exists(), "Run directory should exist"

    # Cleanup
    run_dir = sim.persistence.run_dir
    sim.persistence.close()
    shutil.rmtree(run_dir, ignore_errors=True)

    print("Simulator persistence feature working")


def test_bus_and_messaging():
    """Test that bus still works correctly"""
    print("Testing message bus...")

    from lib.bus import connect_bus, get_bus
    from lib.models import Bar, TimeFrame

    # Connect
    connected = connect_bus()
    assert connected, "Should connect to Redis"

    bus = get_bus()
    assert bus is not None

    # Publish a test bar
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime.utcnow(),
        open=Decimal("150.0"),
        high=Decimal("151.0"),
        low=Decimal("149.5"),
        close=Decimal("150.5"),
        volume=1000000,
        timeframe=TimeFrame.MINUTE
    )

    bus.publish_bar(bar)
    print("  Bar published successfully")

    # Publish system event
    bus.publish_system_event(
        event_type="health_check",
        source="test_system_health",
        data={"status": "ok"}
    )
    print("  System event published successfully")

    bus.disconnect()
    print("Message bus working correctly")


def test_deduplication():
    """Test that deduplication still works"""
    print("Testing deduplication service...")

    from lib.deduplication import get_deduplication_service
    from lib.models import Signal, SignalSide

    dedup = get_deduplication_service()

    # Create test signal
    signal = Signal(
        signal_id=uuid.uuid4(),
        symbol="AAPL",
        timestamp=datetime.utcnow(),
        side=SignalSide.BUY,
        confidence=Decimal("0.85"),
        price=Decimal("150.0"),
        source="test"
    )

    # First time should not be processed
    assert not dedup.is_signal_processed(signal), "First signal should not be processed"

    # Mark as processed
    result = dedup.mark_signal_processed(signal)
    assert result, "Should mark as processed"

    # Second time should be processed
    assert dedup.is_signal_processed(signal), "Second signal should be processed"

    print("Deduplication working correctly")


def test_settings():
    """Test that settings still load correctly"""
    print("Testing settings...")

    from lib.settings import get_settings

    settings = get_settings()

    # Check critical settings
    assert settings.symbols_list is not None
    assert len(settings.symbols_list) > 0
    assert settings.market_timezone == "US/Eastern"
    assert settings.max_orders_per_minute > 0

    print(f"  Symbols: {settings.symbols_list}")
    print(f"  Market TZ: {settings.market_timezone}")
    print(f"  Order rate limit: {settings.max_orders_per_minute}/min")

    print("Settings loaded correctly")


def test_time_utils():
    """Test time utilities"""
    print("Testing time utilities...")

    from lib.time_utils import TimeUtils

    # UTC time
    utc_now = TimeUtils.utc_now()
    assert utc_now is not None
    print(f"  UTC now: {utc_now}")

    # Market time
    market_now = TimeUtils.market_now()
    assert market_now is not None
    print(f"  Market now: {market_now}")

    # Market hours check
    is_market_hours = TimeUtils.is_market_hours()
    print(f"  Is market hours: {is_market_hours}")

    print("Time utilities working correctly")


def test_metrics():
    """Test metrics collection"""
    print("Testing metrics...")

    from lib.metrics_helpers import ServiceMetrics, RiskManagerMetrics

    # Service metrics
    service_metrics = ServiceMetrics("test_service")
    service_metrics.mark_service_start()

    # Risk Manager metrics
    rm_metrics = RiskManagerMetrics()
    rm_metrics.signal_processed("AAPL", "approved")
    rm_metrics.signal_processed("GOOGL", "rejected")

    print("Metrics collection working")


def test_models():
    """Test data models"""
    print("Testing data models...")

    from lib.models import Signal, Bar, OrderIntent, SignalSide, OrderType, TimeFrame

    # Signal
    signal = Signal(
        signal_id=uuid.uuid4(),
        symbol="AAPL",
        timestamp=datetime.utcnow(),
        side=SignalSide.BUY,
        confidence=Decimal("0.85"),
        price=Decimal("150.0"),
        source="test"
    )
    assert signal.symbol == "AAPL"
    print(f"  Signal: {signal.symbol} {signal.side}")

    # Bar
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime.utcnow(),
        open=Decimal("150.0"),
        high=Decimal("151.0"),
        low=Decimal("149.5"),
        close=Decimal("150.5"),
        volume=1000000,
        timeframe=TimeFrame.MINUTE
    )
    assert bar.symbol == "AAPL"
    print(f"  Bar: {bar.symbol} O:{bar.open} C:{bar.close}")

    # OrderIntent
    order = OrderIntent(
        symbol="AAPL",
        timestamp=datetime.utcnow(),
        side=SignalSide.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.MARKET,
        client_order_id="test_order_001",
        signal_source="test"  # Fixed: Required field
    )
    assert order.symbol == "AAPL"
    print(f"  Order: {order.symbol} {order.quantity}")

    print("Data models working correctly")


def test_persistence_standalone():
    """Test persistence module standalone"""
    print("Testing persistence module...")

    from apps.simulator.persist import BacktestPersistence
    import tempfile
    import shutil

    td = tempfile.mkdtemp()

    try:
        # Create persistence
        persistence = BacktestPersistence(run_id="health_check_test", output_dir=td)

        # Save test data
        persistence.save_bar({
            "symbol": "AAPL",
            "timestamp": "2024-01-02T10:00:00",
            "open": 150.0,
            "high": 151.0,
            "low": 149.5,
            "close": 150.5,
            "volume": 1000000,
            "timeframe": "1Min"
        })

        persistence.save_signal({
            "signal_id": str(uuid.uuid4()),
            "symbol": "AAPL",
            "timestamp": "2024-01-02T10:00:00",
            "side": "BUY",
            "confidence": 0.85,
            "price": 150.0,
            "source": "test",
            "metadata": {}
        })

        # Get summary
        summary = persistence.get_summary_stats()
        assert summary["bars_count"] == 1
        assert summary["signals_count"] == 1
        print(f"  Saved 1 bar, 1 signal")

        # Export CSV
        persistence.export_to_csv()
        csv_file = persistence.run_dir / "data" / "bars.csv"
        assert csv_file.exists()
        print(f"  CSV export successful")

        persistence.close()

    finally:
        shutil.rmtree(td, ignore_errors=True)

    print("Persistence module working correctly")


def main():
    """Run all health checks"""
    print("=" * 60)
    print("SYSTEM HEALTH CHECK - EPIC 6 & 7")
    print("=" * 60)
    print(f"Started at: {datetime.now()}")
    print("")

    # Run all tests
    run_test("Import Test", test_imports)
    run_test("Risk Manager + Market Hours", test_risk_manager_market_hours)
    run_test("Risk Manager Signal Processing", test_risk_manager_signal_processing)
    run_test("Backward Compatibility", test_risk_manager_backward_compat)
    run_test("Simulator WITHOUT Persistence", test_simulator_no_persistence)
    run_test("Simulator WITH Persistence", test_simulator_with_persistence)
    run_test("Message Bus", test_bus_and_messaging)
    run_test("Deduplication", test_deduplication)
    run_test("Settings", test_settings)
    run_test("Time Utilities", test_time_utils)
    run_test("Metrics", test_metrics)
    run_test("Data Models", test_models)
    run_test("Persistence Module", test_persistence_standalone)

    # Final report
    print("")
    print("=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    print(f"Total tests: {total_tests}")
    print(f"Passed: {passed_tests} ✅")
    print(f"Failed: {failed_tests} ❌")
    print("")

    if failed_tests == 0:
        print("=" * 60)
        print("✅ ALL HEALTH CHECKS PASSED")
        print("✅ System is healthy")
        print("✅ Epic 6 & 7 integration successful")
        print("=" * 60)
        return 0
    else:
        print("=" * 60)
        print("❌ SOME HEALTH CHECKS FAILED")
        print("❌ Please review failed tests")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
