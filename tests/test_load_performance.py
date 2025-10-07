#!/usr/bin/env python3
"""
tests/test_load_performance.py
Epic 9: Load and Performance Testing - Stress testing and benchmarks
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import os
import tempfile

from lib.models import Signal, SignalSide, OrderIntent, OrderFill, Bar
# from apps.simulator.persist import BacktestPersistence  # API incompatible - see test_epic7_persistence.py


@pytest.mark.slow
@pytest.mark.load
class TestHighVolumeSignals:
    """Test high-volume signal processing"""

    def test_1000_signals_sequential(self):
        """Process 1000 signals sequentially (< 5s)"""
        start_time = time.time()

        signals = []
        base_time = datetime.now(timezone.utc)

        for i in range(1000):
            signal = Signal(
                timestamp=base_time + timedelta(microseconds=i),
                symbol="GOOGL",
                side=SignalSide.BUY if i % 2 == 0 else SignalSide.SELL,
                price=Decimal("100.00") + Decimal(i) / 100,
                confidence=0.8,
                source="load_test"
            )
            signals.append(signal)

        elapsed = time.time() - start_time

        assert len(signals) == 1000
        assert elapsed < 5.0, f"Processing took {elapsed:.2f}s, expected < 5s"

    def test_rapid_signal_timestamps(self):
        """Test signals with microsecond timestamp differences"""
        base_time = datetime.now(timezone.utc)

        signals = [
            Signal(
                timestamp=base_time + timedelta(microseconds=i),
                symbol="GOOGL",
                side=SignalSide.BUY,
                price=Decimal("100.00"),
                confidence=0.8,
                source="test"
            )
            for i in range(100)
        ]

        # Verify all timestamps are unique
        timestamps = [s.timestamp for s in signals]
        assert len(set(timestamps)) == 100


@pytest.mark.slow
@pytest.mark.load
class TestHighVolumeOrders:
    """Test high-volume order creation"""

    def test_1000_order_intents_creation(self):
        """Create 1000 order intents (< 5s)"""
        start_time = time.time()

        orders = []
        for i in range(1000):
            order = OrderIntent(
                symbol="GOOGL",
                side=SignalSide.BUY if i % 2 == 0 else SignalSide.SELL,
                quantity=Decimal("10"),
                client_order_id=f"load_test_{i:06d}",
                signal_source="load_test"
            )
            orders.append(order)

        elapsed = time.time() - start_time

        assert len(orders) == 1000
        assert elapsed < 5.0, f"Order creation took {elapsed:.2f}s, expected < 5s"

    def test_orders_with_large_quantities(self):
        """Test orders with very large quantities"""
        orders = [
            OrderIntent(
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("1000000") + Decimal(i),
                client_order_id=f"large_{i}",
                signal_source="test"
            )
            for i in range(100)
        ]

        assert len(orders) == 100
        assert all(o.quantity >= Decimal("1000000") for o in orders)


@pytest.mark.skip(reason="Persistence API incompatible - Epic 7 has persistence tests")
@pytest.mark.slow
@pytest.mark.load
class TestPersistencePerformance:
    """Test persistence layer performance"""

    def test_save_1000_bars(self):
        """Save 1000 bars to database (< 10s)"""
        pm = BacktestPersistence(run_id="load_test_bars")

        start_time = time.time()
        base_time = datetime.now(timezone.utc)

        bars = [
            Bar(
                timestamp=base_time + timedelta(minutes=i),
                symbol="GOOGL",
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("95"),
                close=Decimal("102"),
                volume=1000 + i
            )
            for i in range(1000)
        ]

        pm.save_bars(bars)
        elapsed = time.time() - start_time

        assert elapsed < 10.0, f"Saving 1000 bars took {elapsed:.2f}s, expected < 10s"

        summary = pm.get_summary()
        assert summary["num_bars"] == 1000

        pm.cleanup()

    def test_save_1000_fills(self):
        """Save 1000 fills to database (< 10s)"""
        pm = BacktestPersistence(run_id="load_test_fills")

        start_time = time.time()
        base_time = datetime.now(timezone.utc)

        fills = [
            OrderFill(
                timestamp=base_time + timedelta(seconds=i),
                symbol="GOOGL",
                side=SignalSide.BUY if i % 2 == 0 else SignalSide.SELL,
                quantity=Decimal("10"),
                price=Decimal("100.00") + Decimal(i) / 100,
                client_order_id=f"fill_{i:06d}"
            )
            for i in range(1000)
        ]

        pm.save_fills(fills)
        elapsed = time.time() - start_time

        assert elapsed < 10.0, f"Saving 1000 fills took {elapsed:.2f}s, expected < 10s"

        summary = pm.get_summary()
        assert summary["num_fills"] == 1000

        pm.cleanup()

    def test_compute_hash_10000_fills(self):
        """Compute SHA256 hash for 10,000 fills (< 5s)"""
        pm = BacktestPersistence(run_id="load_test_hash")

        base_time = datetime.now(timezone.utc)

        # Save fills in batches
        for batch in range(10):
            fills = [
                OrderFill(
                    timestamp=base_time + timedelta(seconds=batch * 1000 + i),
                    symbol="GOOGL",
                    side=SignalSide.BUY,
                    quantity=Decimal("10"),
                    price=Decimal("100.00"),
                    client_order_id=f"hash_{batch}_{i:06d}"
                )
                for i in range(1000)
            ]
            pm.save_fills(fills)

        start_time = time.time()
        hash_result = pm.compute_hash()
        elapsed = time.time() - start_time

        assert elapsed < 5.0, f"Hash computation took {elapsed:.2f}s, expected < 5s"
        assert hash_result is not None
        assert len(hash_result) == 64  # SHA256 hex length

        pm.cleanup()

    def test_csv_export_performance(self):
        """Test CSV export performance (< 5s)"""
        pm = BacktestPersistence(run_id="load_test_csv")

        base_time = datetime.now(timezone.utc)

        # Create 1000 fills
        fills = [
            OrderFill(
                timestamp=base_time + timedelta(seconds=i),
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("10"),
                price=Decimal("100.00"),
                client_order_id=f"csv_{i:06d}"
            )
            for i in range(1000)
        ]
        pm.save_fills(fills)

        start_time = time.time()
        csv_path = pm.export_to_csv()
        elapsed = time.time() - start_time

        assert elapsed < 5.0, f"CSV export took {elapsed:.2f}s, expected < 5s"
        assert csv_path is not None
        assert os.path.exists(csv_path)

        pm.cleanup()


@pytest.mark.skip(reason="Persistence API incompatible - Epic 7 has persistence tests")
@pytest.mark.slow
@pytest.mark.load
class TestMemoryEfficiency:
    """Test memory usage and efficiency"""

    def test_large_signal_list_memory(self):
        """Test memory usage with large signal list"""
        signals = [
            Signal(
                timestamp=datetime.now(timezone.utc) + timedelta(seconds=i),
                symbol="GOOGL",
                side=SignalSide.BUY,
                price=Decimal("100.00"),
                confidence=0.8,
                source="memory_test"
            )
            for i in range(10000)
        ]

        assert len(signals) == 10000

        # Check that signals are actually created
        assert all(isinstance(s, Signal) for s in signals)

    def test_database_file_size(self):
        """Test database file size for large dataset"""
        pm = BacktestPersistence(run_id="load_test_filesize")

        base_time = datetime.now(timezone.utc)

        # Create 1000 bars
        bars = [
            Bar(
                timestamp=base_time + timedelta(minutes=i),
                symbol="GOOGL",
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("95"),
                close=Decimal("102"),
                volume=1000
            )
            for i in range(1000)
        ]
        pm.save_bars(bars)

        # Create 1000 fills
        fills = [
            OrderFill(
                timestamp=base_time + timedelta(seconds=i),
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("10"),
                price=Decimal("100.00"),
                client_order_id=f"size_{i:06d}"
            )
            for i in range(1000)
        ]
        pm.save_fills(fills)

        # Create 1000 signals
        signals = [
            Signal(
                timestamp=base_time + timedelta(seconds=i),
                symbol="GOOGL",
                side=SignalSide.BUY,
                price=Decimal("100.00"),
                confidence=0.8,
                source="size_test"
            )
            for i in range(1000)
        ]
        pm.save_signals(signals)

        # Check database file size (should be reasonable)
        db_path = pm.db_path
        db_size_mb = os.path.getsize(db_path) / (1024 * 1024)

        assert db_size_mb < 50, f"Database size {db_size_mb:.2f} MB exceeds 50 MB for 3000 records"

        pm.cleanup()


@pytest.mark.skip(reason="Persistence API incompatible - Epic 7 has persistence tests")
@pytest.mark.slow
@pytest.mark.load
class TestConcurrentOperations:
    """Test concurrent database operations"""

    def test_rapid_persistence_writes(self):
        """Test rapid consecutive writes"""
        pm = BacktestPersistence(run_id="load_test_concurrent")

        start_time = time.time()
        base_time = datetime.now(timezone.utc)

        # Rapidly write bars and signals
        for i in range(50):
            bars = [
                Bar(
                    timestamp=base_time + timedelta(seconds=i * 10 + j),
                    symbol="GOOGL",
                    open=Decimal("100"),
                    high=Decimal("105"),
                    low=Decimal("95"),
                    close=Decimal("102"),
                    volume=1000
                )
                for j in range(10)
            ]
            pm.save_bars(bars)

            signals = [
                Signal(
                    timestamp=base_time + timedelta(seconds=i * 10 + j),
                    symbol="GOOGL",
                    side=SignalSide.BUY,
                    price=Decimal("100.00"),
                    confidence=0.8,
                    source="concurrent_test"
                )
                for j in range(10)
            ]
            pm.save_signals(signals)

        elapsed = time.time() - start_time

        # Should handle 1000 writes efficiently
        assert elapsed < 15.0, f"Concurrent writes took {elapsed:.2f}s, expected < 15s"

        summary = pm.get_summary()
        assert summary["num_bars"] == 500
        assert summary["num_signals"] == 500

        pm.cleanup()


@pytest.mark.skip(reason="Persistence API incompatible - Epic 7 has persistence tests")
@pytest.mark.slow
@pytest.mark.load
class TestDataIntegrity:
    """Test data integrity under load"""

    def test_hash_consistency_under_load(self):
        """Test that hash is consistent across multiple runs"""
        hashes = []

        for run in range(5):
            pm = BacktestPersistence(run_id=f"integrity_test_{run}")

            base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

            # Create identical fills
            fills = [
                OrderFill(
                    timestamp=base_time + timedelta(seconds=i),
                    symbol="GOOGL",
                    side=SignalSide.BUY,
                    quantity=Decimal("10"),
                    price=Decimal("100.00"),
                    client_order_id=f"integrity_{i:06d}"
                )
                for i in range(100)
            ]
            pm.save_fills(fills)

            hash_result = pm.compute_hash()
            hashes.append(hash_result)

            pm.cleanup()

        # All hashes should be identical
        assert len(set(hashes)) == 1, "Hashes are not consistent across runs"

    def test_no_data_loss(self):
        """Test that no data is lost in high-volume scenario"""
        pm = BacktestPersistence(run_id="load_test_no_loss")

        base_time = datetime.now(timezone.utc)
        num_fills = 1000

        # Save fills
        fills = [
            OrderFill(
                timestamp=base_time + timedelta(seconds=i),
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("10"),
                price=Decimal("100.00") + Decimal(i) / 100,
                client_order_id=f"loss_{i:06d}"
            )
            for i in range(num_fills)
        ]
        pm.save_fills(fills)

        # Verify all fills were saved
        loaded_fills = pm.load_fills()
        assert len(loaded_fills) == num_fills

        # Verify unique client_order_ids
        client_ids = [f.client_order_id for f in loaded_fills]
        assert len(set(client_ids)) == num_fills

        pm.cleanup()


@pytest.mark.slow
@pytest.mark.load
class TestPerformanceBenchmarks:
    """Benchmark key operations"""

    def test_signal_creation_benchmark(self):
        """Benchmark: Create 1000 signals"""
        start_time = time.time()

        signals = [
            Signal(
                timestamp=datetime.now(timezone.utc) + timedelta(microseconds=i),
                symbol="GOOGL",
                side=SignalSide.BUY,
                price=Decimal("100.00"),
                confidence=0.8,
                source="benchmark"
            )
            for i in range(1000)
        ]

        elapsed = time.time() - start_time
        throughput = 1000 / elapsed

        assert len(signals) == 1000
        assert throughput > 1000, f"Signal creation: {throughput:.0f}/s, expected > 1000/s"

    def test_order_creation_benchmark(self):
        """Benchmark: Create 1000 orders"""
        start_time = time.time()

        orders = [
            OrderIntent(
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("10"),
                client_order_id=f"bench_{i:06d}",
                signal_source="benchmark"
            )
            for i in range(1000)
        ]

        elapsed = time.time() - start_time
        throughput = 1000 / elapsed

        assert len(orders) == 1000
        assert throughput > 1000, f"Order creation: {throughput:.0f}/s, expected > 1000/s"
