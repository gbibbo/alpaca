#!/usr/bin/env python3
"""
tests/test_epic7_persistence.py
Tests for Epic 7: Persistence of backtest results and PnL
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import tempfile
import shutil
from datetime import datetime
from decimal import Decimal

from apps.simulator.persist import BacktestPersistence, create_backtest_persistence


class TestBacktestPersistence:
    """Test BacktestPersistence functionality"""

    def setup_method(self):
        """Setup test environment"""
        # Create temporary directory for tests
        self.temp_dir = tempfile.mkdtemp()
        self.persistence = BacktestPersistence(run_id="test_run_001", output_dir=self.temp_dir)

    def teardown_method(self):
        """Cleanup test environment"""
        self.persistence.close()
        # Clean up temp directory
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initialization(self):
        """Test persistence initialization"""
        assert self.persistence.run_id == "test_run_001"
        assert self.persistence.run_dir.exists()
        assert self.persistence.db_path.exists()
        assert (self.persistence.run_dir / "data").exists()

    def test_save_bar(self):
        """Test saving a single bar"""
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

        self.persistence.save_bar(bar)

        # Verify bar was saved
        cursor = self.persistence.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bars WHERE run_id = ?", (self.persistence.run_id,))
        count = cursor.fetchone()[0]
        assert count == 1

    def test_save_bars_batch(self):
        """Test saving multiple bars in batch"""
        bars = [
            {
                "symbol": "AAPL",
                "timestamp": datetime(2024, 1, 2, 10, i),
                "open": 150.0 + i,
                "high": 151.0 + i,
                "low": 149.5 + i,
                "close": 150.5 + i,
                "volume": 1000000,
                "timeframe": "1Min"
            }
            for i in range(10)
        ]

        self.persistence.save_bars_batch(bars)

        # Verify bars were saved
        cursor = self.persistence.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bars WHERE run_id = ?", (self.persistence.run_id,))
        count = cursor.fetchone()[0]
        assert count == 10

    def test_save_signal(self):
        """Test saving a signal"""
        signal = {
            "signal_id": "sig_001",
            "symbol": "AAPL",
            "timestamp": "2024-01-02T10:00:00+00:00",
            "side": "BUY",
            "confidence": 0.85,
            "price": 150.0,
            "source": "random_50_50",
            "metadata": {"test": "value"}
        }

        self.persistence.save_signal(signal)

        # Verify signal was saved
        cursor = self.persistence.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM signals WHERE run_id = ?", (self.persistence.run_id,))
        count = cursor.fetchone()[0]
        assert count == 1

    def test_save_order(self):
        """Test saving an order"""
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

        self.persistence.save_order(order)

        # Verify order was saved
        cursor = self.persistence.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM orders WHERE run_id = ?", (self.persistence.run_id,))
        count = cursor.fetchone()[0]
        assert count == 1

    def test_save_fill(self):
        """Test saving a fill"""
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

        self.persistence.save_fill(fill)

        # Verify fill was saved
        cursor = self.persistence.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM fills WHERE run_id = ?", (self.persistence.run_id,))
        count = cursor.fetchone()[0]
        assert count == 1

    def test_save_equity_snapshot(self):
        """Test saving equity snapshot"""
        equity = {
            "timestamp": "2024-01-02T10:00:00+00:00",
            "equity": 100500.0,
            "cash": 50000.0,
            "positions_value": 50500.0,
            "total_pnl": 500.0,
            "realized_pnl": 200.0,
            "unrealized_pnl": 300.0
        }

        self.persistence.save_equity_snapshot(equity)

        # Verify equity was saved
        cursor = self.persistence.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM equity WHERE run_id = ?", (self.persistence.run_id,))
        count = cursor.fetchone()[0]
        assert count == 1

    def test_save_position_snapshot(self):
        """Test saving position snapshot"""
        position = {
            "timestamp": "2024-01-02T10:00:00+00:00",
            "symbol": "AAPL",
            "quantity": 10,
            "avg_entry_price": 150.0,
            "current_price": 151.0,
            "market_value": 1510.0,
            "unrealized_pnl": 10.0,
            "side": "LONG"
        }

        self.persistence.save_position_snapshot(position)

        # Verify position was saved
        cursor = self.persistence.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM positions WHERE run_id = ?", (self.persistence.run_id,))
        count = cursor.fetchone()[0]
        assert count == 1

    def test_save_metadata(self):
        """Test saving metadata"""
        self.persistence.save_metadata("test_key", "test_value")
        self.persistence.save_metadata("test_dict", {"nested": "data"})

        # Verify metadata was saved
        cursor = self.persistence.conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = ?", ("test_key",))
        value = cursor.fetchone()[0]
        assert value == "test_value"

    def test_get_summary_stats(self):
        """Test summary statistics generation"""
        # Add some test data
        self.persistence.save_bar({
            "symbol": "AAPL",
            "timestamp": "2024-01-02T10:00:00+00:00",
            "open": 150.0,
            "high": 151.0,
            "low": 149.5,
            "close": 150.5,
            "volume": 1000000,
            "timeframe": "1Min"
        })

        self.persistence.save_signal({
            "signal_id": "sig_001",
            "symbol": "AAPL",
            "timestamp": "2024-01-02T10:00:00+00:00",
            "side": "BUY",
            "confidence": 0.85,
            "price": 150.0,
            "source": "random_50_50",
            "metadata": {}
        })

        self.persistence.save_equity_snapshot({
            "timestamp": "2024-01-02T10:00:00+00:00",
            "equity": 100000.0,
            "cash": 100000.0,
            "positions_value": 0.0,
            "total_pnl": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0
        })

        summary = self.persistence.get_summary_stats()

        assert summary["run_id"] == "test_run_001"
        assert summary["bars_count"] == 1
        assert summary["signals_count"] == 1
        assert summary["equity"]["initial"] == 100000.0

    def test_export_to_csv(self):
        """Test CSV export"""
        # Add some test data
        self.persistence.save_bar({
            "symbol": "AAPL",
            "timestamp": "2024-01-02T10:00:00+00:00",
            "open": 150.0,
            "high": 151.0,
            "low": 149.5,
            "close": 150.5,
            "volume": 1000000,
            "timeframe": "1Min"
        })

        # Export
        self.persistence.export_to_csv()

        # Verify CSV file exists
        csv_file = self.persistence.run_dir / "data" / "bars.csv"
        assert csv_file.exists()

        # Verify CSV content
        with open(csv_file, 'r') as f:
            lines = f.readlines()
            assert len(lines) == 2  # Header + 1 data row

    def test_save_summary(self):
        """Test summary file generation"""
        # Add some test data
        self.persistence.save_equity_snapshot({
            "timestamp": "2024-01-02T10:00:00+00:00",
            "equity": 100000.0,
            "cash": 100000.0,
            "positions_value": 0.0,
            "total_pnl": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0
        })

        # Save summary
        summary = self.persistence.save_summary()

        # Verify summary file exists
        summary_file = self.persistence.run_dir / "summary.json"
        assert summary_file.exists()

        # Verify summary content
        import json
        with open(summary_file, 'r') as f:
            loaded_summary = json.load(f)
            assert loaded_summary["run_id"] == "test_run_001"
            assert "metadata" in loaded_summary


class TestReproducibility:
    """Test reproducibility features"""

    def setup_method(self):
        """Setup test environment"""
        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        """Cleanup test environment"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_compute_hash_consistency(self):
        """Test that hash computation is consistent"""
        # Create two identical runs
        persistence1 = BacktestPersistence(run_id="test_hash_1", output_dir=self.temp_dir)
        persistence2 = BacktestPersistence(run_id="test_hash_2", output_dir=self.temp_dir)

        # Add identical fills to both
        fills = [
            {
                "fill_id": f"fill_{i:03d}",
                "symbol": "AAPL",
                "timestamp": f"2024-01-02T10:{i:02d}:00+00:00",
                "side": "BUY",
                "quantity": 10,
                "price": 150.0 + i * 0.1,
                "commission": 1.0,
                "metadata": {}
            }
            for i in range(5)
        ]

        for fill in fills:
            persistence1.save_fill(fill)
            persistence2.save_fill(fill)

        # Compute hashes
        hash1 = persistence1.compute_hash()
        hash2 = persistence2.compute_hash()

        # Hashes should be identical
        assert hash1 == hash2

        persistence1.close()
        persistence2.close()

    def test_compute_hash_different(self):
        """Test that different results produce different hashes"""
        persistence1 = BacktestPersistence(run_id="test_diff_1", output_dir=self.temp_dir)
        persistence2 = BacktestPersistence(run_id="test_diff_2", output_dir=self.temp_dir)

        # Add different fills
        persistence1.save_fill({
            "fill_id": "fill_001",
            "symbol": "AAPL",
            "timestamp": "2024-01-02T10:00:00+00:00",
            "side": "BUY",
            "quantity": 10,
            "price": 150.0,
            "commission": 1.0,
            "metadata": {}
        })

        persistence2.save_fill({
            "fill_id": "fill_002",
            "symbol": "AAPL",
            "timestamp": "2024-01-02T10:00:00+00:00",
            "side": "BUY",
            "quantity": 20,  # Different quantity
            "price": 150.0,
            "commission": 1.0,
            "metadata": {}
        })

        # Compute hashes
        hash1 = persistence1.compute_hash()
        hash2 = persistence2.compute_hash()

        # Hashes should be different
        assert hash1 != hash2

        persistence1.close()
        persistence2.close()


class TestContextManager:
    """Test context manager functionality"""

    def test_context_manager(self):
        """Test using persistence as context manager"""
        temp_dir = tempfile.mkdtemp()

        try:
            with BacktestPersistence(run_id="test_context", output_dir=temp_dir) as persistence:
                persistence.save_bar({
                    "symbol": "AAPL",
                    "timestamp": "2024-01-02T10:00:00+00:00",
                    "open": 150.0,
                    "high": 151.0,
                    "low": 149.5,
                    "close": 150.5,
                    "volume": 1000000,
                    "timeframe": "1Min"
                })

                # Verify data was saved
                cursor = persistence.conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM bars")
                count = cursor.fetchone()[0]
                assert count == 1

            # After context exit, connection should be closed
            # (can't easily test this without accessing private state)

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestFactoryFunction:
    """Test factory function"""

    def test_create_backtest_persistence(self):
        """Test factory function"""
        temp_dir = tempfile.mkdtemp()

        try:
            persistence = create_backtest_persistence(run_id="test_factory", output_dir=temp_dir)
            assert persistence.run_id == "test_factory"
            assert persistence.run_dir.exists()
            persistence.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestDatabaseSchema:
    """Test database schema and indexes"""

    def test_all_tables_created(self):
        """Test that all expected tables are created"""
        temp_dir = tempfile.mkdtemp()

        try:
            persistence = BacktestPersistence(run_id="test_schema", output_dir=temp_dir)

            cursor = persistence.conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            expected_tables = ["bars", "signals", "orders", "fills", "equity", "positions", "metadata"]
            for table in expected_tables:
                assert table in tables

            persistence.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_indexes_created(self):
        """Test that indexes are created"""
        temp_dir = tempfile.mkdtemp()

        try:
            persistence = BacktestPersistence(run_id="test_indexes", output_dir=temp_dir)

            cursor = persistence.conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]

            # Should have indexes for performance
            assert len(indexes) > 0
            assert any("bars" in idx for idx in indexes)
            assert any("signals" in idx for idx in indexes)

            persistence.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
