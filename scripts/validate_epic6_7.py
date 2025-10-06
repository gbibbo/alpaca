#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/validate_epic6_7.py
Validation script for Epic 6 (Market Hours) and Epic 7 (Persistence)
Simple validation without pytest dependency
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import tempfile
import shutil
from datetime import datetime, time
import pytz

# Test imports
try:
    from apps.risk_manager.market_hours import MarketCalendar, MarketHoursValidator
    from apps.simulator.persist import BacktestPersistence
    print("[OK] All imports successful")
except Exception as e:
    print(f"[FAIL] Import failed: {e}")
    sys.exit(1)


def test_epic6_market_hours():
    """Test Epic 6: Market Hours and Calendar Validation"""
    print("\n" + "="*60)
    print("Testing Epic 6: Market Hours and Calendar Validation")
    print("="*60)

    # Test 1: Calendar initialization
    print("\n1. Testing calendar initialization...")
    try:
        calendar = MarketCalendar()
        assert calendar.timezone is not None
        print("   ✅ Calendar initialized successfully")
    except Exception as e:
        print(f"   ❌ Calendar initialization failed: {e}")
        return False

    # Test 2: Holiday detection
    print("\n2. Testing holiday detection...")
    try:
        new_years = datetime(2024, 1, 1, 12, 0)
        is_holiday, name = calendar.is_holiday(new_years)
        assert is_holiday is True
        assert name == "New Year's Day"
        print(f"   ✅ Holiday detected correctly: {name}")
    except Exception as e:
        print(f"   ❌ Holiday detection failed: {e}")
        return False

    # Test 3: Early close detection
    print("\n3. Testing early close detection...")
    try:
        black_friday = datetime(2024, 11, 29, 12, 0)
        is_early, reason = calendar.is_early_close(black_friday)
        assert is_early is True
        print(f"   ✅ Early close detected: {reason}")
    except Exception as e:
        print(f"   ❌ Early close detection failed: {e}")
        return False

    # Test 4: Market hours calculation
    print("\n4. Testing market hours calculation...")
    try:
        regular_day = datetime(2024, 1, 2, 12, 0)
        open_time, close_time = calendar.get_market_hours(regular_day)
        assert open_time == time(9, 30)
        assert close_time == time(16, 0)
        print(f"   ✅ Regular hours: {open_time} - {close_time}")

        early_close_day = datetime(2024, 11, 29, 12, 0)
        open_time, close_time = calendar.get_market_hours(early_close_day)
        assert close_time == time(13, 0)
        print(f"   ✅ Early close hours: {open_time} - {close_time}")
    except Exception as e:
        print(f"   ❌ Market hours calculation failed: {e}")
        return False

    # Test 5: Trading time validation
    print("\n5. Testing trading time validation...")
    try:
        et = pytz.timezone("US/Eastern")

        # Valid trading time
        trading_time = et.localize(datetime(2024, 1, 2, 10, 30))
        is_valid, reason = calendar.validate_trading_time(trading_time)
        assert is_valid is True
        print(f"   ✅ Valid trading time: {reason}")

        # Weekend
        weekend = et.localize(datetime(2024, 1, 6, 10, 0))
        is_valid, reason = calendar.validate_trading_time(weekend)
        assert is_valid is False
        print(f"   ✅ Weekend rejected: {reason}")

        # Holiday
        holiday = et.localize(datetime(2024, 1, 1, 10, 0))
        is_valid, reason = calendar.validate_trading_time(holiday)
        assert is_valid is False
        print(f"   ✅ Holiday rejected: {reason}")
    except Exception as e:
        print(f"   ❌ Trading time validation failed: {e}")
        return False

    # Test 6: Market Hours Validator
    print("\n6. Testing MarketHoursValidator...")
    try:
        validator = MarketHoursValidator()
        assert validator.calendar is not None

        # Get stats
        stats = validator.get_stats()
        assert "current_time" in stats
        assert "timezone" in stats
        assert "is_open" in stats
        print(f"   ✅ Validator initialized and stats generated")
        print(f"      Current status: {stats['status']}")
    except Exception as e:
        print(f"   ❌ Validator test failed: {e}")
        return False

    print("\n✅ Epic 6 validation complete!")
    return True


def test_epic7_persistence():
    """Test Epic 7: Persistence of Backtest Results"""
    print("\n" + "="*60)
    print("Testing Epic 7: Persistence of Backtest Results")
    print("="*60)

    temp_dir = tempfile.mkdtemp()

    try:
        # Test 1: Initialization
        print("\n1. Testing persistence initialization...")
        try:
            persistence = BacktestPersistence(run_id="test_run_001", output_dir=temp_dir)
            assert persistence.run_id == "test_run_001"
            assert persistence.run_dir.exists()
            assert persistence.db_path.exists()
            print(f"   ✅ Persistence initialized: {persistence.run_dir}")
        except Exception as e:
            print(f"   ❌ Initialization failed: {e}")
            return False

        # Test 2: Save bar
        print("\n2. Testing bar persistence...")
        try:
            bar = {
                "symbol": "AAPL",
                "timestamp": "2024-01-02T10:00:00+00:00",
                "open": 150.0,
                "high": 151.0,
                "low": 149.5,
                "close": 150.5,
                "volume": 1000000,
                "timeframe": "1Min"
            }
            persistence.save_bar(bar)

            cursor = persistence.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM bars WHERE run_id = ?", (persistence.run_id,))
            count = cursor.fetchone()[0]
            assert count == 1
            print("   ✅ Bar saved successfully")
        except Exception as e:
            print(f"   ❌ Bar persistence failed: {e}")
            return False

        # Test 3: Save signal
        print("\n3. Testing signal persistence...")
        try:
            signal = {
                "signal_id": "sig_001",
                "symbol": "AAPL",
                "timestamp": "2024-01-02T10:00:00+00:00",
                "side": "BUY",
                "confidence": 0.85,
                "price": 150.0,
                "source": "random_50_50",
                "metadata": {}
            }
            persistence.save_signal(signal)

            cursor = persistence.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM signals WHERE run_id = ?", (persistence.run_id,))
            count = cursor.fetchone()[0]
            assert count == 1
            print("   ✅ Signal saved successfully")
        except Exception as e:
            print(f"   ❌ Signal persistence failed: {e}")
            return False

        # Test 4: Save order
        print("\n4. Testing order persistence...")
        try:
            order = {
                "order_id": "order_001",
                "client_order_id": "client_order_001",
                "symbol": "AAPL",
                "timestamp": "2024-01-02T10:00:00+00:00",
                "side": "BUY",
                "quantity": 10,
                "order_type": "MARKET",
                "price": 150.0,
                "status": "FILLED",
                "signal_source": "random_50_50",
                "metadata": {}
            }
            persistence.save_order(order)

            cursor = persistence.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM orders WHERE run_id = ?", (persistence.run_id,))
            count = cursor.fetchone()[0]
            assert count == 1
            print("   ✅ Order saved successfully")
        except Exception as e:
            print(f"   ❌ Order persistence failed: {e}")
            return False

        # Test 5: Save fill
        print("\n5. Testing fill persistence...")
        try:
            fill = {
                "fill_id": "fill_001",
                "order_id": "order_001",
                "client_order_id": "client_order_001",
                "symbol": "AAPL",
                "timestamp": "2024-01-02T10:00:00+00:00",
                "side": "BUY",
                "quantity": 10,
                "price": 150.0,
                "commission": 1.0,
                "metadata": {}
            }
            persistence.save_fill(fill)

            cursor = persistence.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM fills WHERE run_id = ?", (persistence.run_id,))
            count = cursor.fetchone()[0]
            assert count == 1
            print("   ✅ Fill saved successfully")
        except Exception as e:
            print(f"   ❌ Fill persistence failed: {e}")
            return False

        # Test 6: Save equity snapshot
        print("\n6. Testing equity snapshot persistence...")
        try:
            equity = {
                "timestamp": "2024-01-02T10:00:00+00:00",
                "equity": 100500.0,
                "cash": 50000.0,
                "positions_value": 50500.0,
                "total_pnl": 500.0,
                "realized_pnl": 200.0,
                "unrealized_pnl": 300.0
            }
            persistence.save_equity_snapshot(equity)

            cursor = persistence.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM equity WHERE run_id = ?", (persistence.run_id,))
            count = cursor.fetchone()[0]
            assert count == 1
            print("   ✅ Equity snapshot saved successfully")
        except Exception as e:
            print(f"   ❌ Equity snapshot persistence failed: {e}")
            return False

        # Test 7: Get summary stats
        print("\n7. Testing summary statistics...")
        try:
            summary = persistence.get_summary_stats()
            assert summary["run_id"] == "test_run_001"
            assert summary["bars_count"] == 1
            assert summary["signals_count"] == 1
            assert summary["orders_count"] == 1
            assert summary["fills_count"] == 1
            print("   ✅ Summary statistics generated")
            print(f"      Bars: {summary['bars_count']}, Signals: {summary['signals_count']}")
            print(f"      Orders: {summary['orders_count']}, Fills: {summary['fills_count']}")
        except Exception as e:
            print(f"   ❌ Summary stats failed: {e}")
            return False

        # Test 8: Save summary file
        print("\n8. Testing summary file generation...")
        try:
            summary = persistence.save_summary()
            summary_file = persistence.run_dir / "summary.json"
            assert summary_file.exists()
            print(f"   ✅ Summary file saved: {summary_file}")
        except Exception as e:
            print(f"   ❌ Summary file generation failed: {e}")
            return False

        # Test 9: Export to CSV
        print("\n9. Testing CSV export...")
        try:
            persistence.export_to_csv()
            csv_file = persistence.run_dir / "data" / "bars.csv"
            assert csv_file.exists()
            print(f"   ✅ CSV export successful: {persistence.run_dir / 'data'}")
        except Exception as e:
            print(f"   ❌ CSV export failed: {e}")
            return False

        # Test 10: Compute hash for reproducibility
        print("\n10. Testing reproducibility hash...")
        try:
            results_hash = persistence.compute_hash()
            assert len(results_hash) == 64  # SHA256 hex digest
            print(f"   ✅ Hash computed: {results_hash[:16]}...")
        except Exception as e:
            print(f"   ❌ Hash computation failed: {e}")
            return False

        # Test 11: Test reproducibility (same data = same hash)
        print("\n11. Testing hash reproducibility...")
        try:
            persistence2 = BacktestPersistence(run_id="test_run_002", output_dir=temp_dir)
            persistence2.save_fill(fill)  # Same fill as before
            hash2 = persistence2.compute_hash()

            # Hashes should be identical for same data
            assert results_hash == hash2
            print("   ✅ Reproducibility verified (same data = same hash)")
            persistence2.close()
        except Exception as e:
            print(f"   ❌ Reproducibility test failed: {e}")
            return False

        persistence.close()
        print("\n✅ Epic 7 validation complete!")
        return True

    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    """Run all validations"""
    print("\n" + "="*60)
    print("Epic 6 & 7 Validation Suite")
    print("="*60)

    results = []

    # Test Epic 6
    try:
        result_epic6 = test_epic6_market_hours()
        results.append(("Epic 6 - Market Hours", result_epic6))
    except Exception as e:
        print(f"\n❌ Epic 6 validation crashed: {e}")
        results.append(("Epic 6 - Market Hours", False))

    # Test Epic 7
    try:
        result_epic7 = test_epic7_persistence()
        results.append(("Epic 7 - Persistence", result_epic7))
    except Exception as e:
        print(f"\n❌ Epic 7 validation crashed: {e}")
        results.append(("Epic 7 - Persistence", False))

    # Summary
    print("\n" + "="*60)
    print("VALIDATION SUMMARY")
    print("="*60)

    all_passed = True
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{name:30} {status}")
        if not passed:
            all_passed = False

    print("="*60)

    if all_passed:
        print("\n🎉 All validations passed!")
        return 0
    else:
        print("\n⚠️  Some validations failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
