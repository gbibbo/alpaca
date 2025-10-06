#!/usr/bin/env python3
"""
apps/simulator/persist.py
Persistence layer for backtest and simulation results
Saves bars, signals, orders, fills, and equity to Parquet/SQLite
Supports reproducibility and PnL analysis
"""

import logging
import sqlite3
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
from decimal import Decimal
import uuid

logger = logging.getLogger(__name__)


class BacktestPersistence:
    """
    Persistence manager for backtest results
    Saves data to SQLite and optionally to CSV/Parquet
    """

    def __init__(self, run_id: Optional[str] = None, output_dir: str = "out"):
        self.run_id = run_id or self._generate_run_id()
        self.output_dir = Path(output_dir)
        self.run_dir = self.output_dir / self.run_id

        # Create output directory structure
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "data").mkdir(exist_ok=True)

        # Initialize SQLite database
        self.db_path = self.run_dir / "backtest.db"
        self.conn = None
        self._init_database()

        # Metadata
        self.metadata = {
            "run_id": self.run_id,
            "created_at": datetime.utcnow().isoformat(),
            "version": "1.0",
        }

        logger.info(f"Backtest persistence initialized: {self.run_dir}")

    def _generate_run_id(self) -> str:
        """Generate unique run ID"""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        return f"run_{timestamp}_{unique_id}"

    def _init_database(self):
        """Initialize SQLite database schema"""
        self.conn = sqlite3.connect(str(self.db_path))
        cursor = self.conn.cursor()

        # Bars table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                timeframe TEXT
            )
        """)

        # Signals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                signal_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                side TEXT NOT NULL,
                confidence REAL NOT NULL,
                price REAL,
                source TEXT,
                metadata TEXT
            )
        """)

        # Orders table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                order_id TEXT UNIQUE,
                client_order_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                status TEXT NOT NULL,
                signal_source TEXT,
                metadata TEXT
            )
        """)

        # Fills table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                fill_id TEXT UNIQUE NOT NULL,
                order_id TEXT,
                client_order_id TEXT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                commission REAL DEFAULT 0,
                metadata TEXT
            )
        """)

        # Equity curve table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                equity REAL NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0
            )
        """)

        # Positions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                avg_entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                market_value REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                side TEXT NOT NULL
            )
        """)

        # Metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Create indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bars_symbol_ts ON bars(symbol, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON signals(symbol, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_symbol_ts ON orders(symbol, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fills_symbol_ts ON fills(symbol, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(timestamp)")

        self.conn.commit()
        logger.info(f"Database initialized: {self.db_path}")

    def save_bar(self, bar: Dict):
        """Save a single bar"""
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT INTO bars (run_id, symbol, timestamp, open, high, low, close, volume, timeframe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.run_id,
            bar["symbol"],
            bar["timestamp"],
            float(bar["open"]),
            float(bar["high"]),
            float(bar["low"]),
            float(bar["close"]),
            int(bar["volume"]),
            bar.get("timeframe", "1Min")
        ))

        self.conn.commit()

    def save_bars_batch(self, bars: List[Dict]):
        """Save multiple bars in batch"""
        cursor = self.conn.cursor()

        data = [
            (
                self.run_id,
                bar["symbol"],
                bar["timestamp"] if isinstance(bar["timestamp"], str) else bar["timestamp"].isoformat(),
                float(bar["open"]),
                float(bar["high"]),
                float(bar["low"]),
                float(bar["close"]),
                int(bar["volume"]),
                bar.get("timeframe", "1Min")
            )
            for bar in bars
        ]

        cursor.executemany("""
            INSERT INTO bars (run_id, symbol, timestamp, open, high, low, close, volume, timeframe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, data)

        self.conn.commit()
        logger.info(f"Saved {len(bars)} bars")

    def save_signal(self, signal: Dict):
        """Save a signal"""
        cursor = self.conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO signals (run_id, signal_id, symbol, timestamp, side, confidence, price, source, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.run_id,
                signal["signal_id"],
                signal["symbol"],
                signal["timestamp"] if isinstance(signal["timestamp"], str) else signal["timestamp"].isoformat(),
                signal["side"],
                float(signal["confidence"]),
                float(signal["price"]) if signal.get("price") else None,
                signal.get("source"),
                json.dumps(signal.get("metadata", {}))
            ))

            self.conn.commit()
        except sqlite3.IntegrityError:
            logger.warning(f"Signal {signal['signal_id']} already exists, skipping")

    def save_order(self, order: Dict):
        """Save an order"""
        cursor = self.conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO orders (run_id, order_id, client_order_id, symbol, timestamp, side,
                                  quantity, order_type, price, status, signal_source, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.run_id,
                order.get("order_id"),
                order["client_order_id"],
                order["symbol"],
                order["timestamp"] if isinstance(order["timestamp"], str) else order["timestamp"].isoformat(),
                order["side"],
                float(order["quantity"]),
                order["order_type"],
                float(order["price"]) if order.get("price") else None,
                order["status"],
                order.get("signal_source"),
                json.dumps(order.get("metadata", {}))
            ))

            self.conn.commit()
        except sqlite3.IntegrityError:
            logger.warning(f"Order {order['client_order_id']} already exists, skipping")

    def save_fill(self, fill: Dict):
        """Save a fill"""
        cursor = self.conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO fills (run_id, fill_id, order_id, client_order_id, symbol, timestamp,
                                 side, quantity, price, commission, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.run_id,
                fill["fill_id"],
                fill.get("order_id"),
                fill.get("client_order_id"),
                fill["symbol"],
                fill["timestamp"] if isinstance(fill["timestamp"], str) else fill["timestamp"].isoformat(),
                fill["side"],
                float(fill["quantity"]),
                float(fill["price"]),
                float(fill.get("commission", 0)),
                json.dumps(fill.get("metadata", {}))
            ))

            self.conn.commit()
        except sqlite3.IntegrityError:
            logger.warning(f"Fill {fill['fill_id']} already exists, skipping")

    def save_equity_snapshot(self, equity: Dict):
        """Save equity snapshot"""
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT INTO equity (run_id, timestamp, equity, cash, positions_value,
                              total_pnl, realized_pnl, unrealized_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.run_id,
            equity["timestamp"] if isinstance(equity["timestamp"], str) else equity["timestamp"].isoformat(),
            float(equity["equity"]),
            float(equity["cash"]),
            float(equity.get("positions_value", 0)),
            float(equity.get("total_pnl", 0)),
            float(equity.get("realized_pnl", 0)),
            float(equity.get("unrealized_pnl", 0))
        ))

        self.conn.commit()

    def save_position_snapshot(self, position: Dict):
        """Save position snapshot"""
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT INTO positions (run_id, timestamp, symbol, quantity, avg_entry_price,
                                 current_price, market_value, unrealized_pnl, side)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.run_id,
            position["timestamp"] if isinstance(position["timestamp"], str) else position["timestamp"].isoformat(),
            position["symbol"],
            float(position["quantity"]),
            float(position["avg_entry_price"]),
            float(position["current_price"]),
            float(position["market_value"]),
            float(position["unrealized_pnl"]),
            position["side"]
        ))

        self.conn.commit()

    def save_metadata(self, key: str, value: Any):
        """Save metadata key-value pair"""
        cursor = self.conn.cursor()

        value_str = json.dumps(value) if not isinstance(value, str) else value

        cursor.execute("""
            INSERT OR REPLACE INTO metadata (key, value)
            VALUES (?, ?)
        """, (key, value_str))

        self.conn.commit()

    def get_summary_stats(self) -> Dict:
        """Calculate summary statistics from persisted data"""
        cursor = self.conn.cursor()

        # Get counts
        cursor.execute(f"SELECT COUNT(*) FROM bars WHERE run_id = ?", (self.run_id,))
        bars_count = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM signals WHERE run_id = ?", (self.run_id,))
        signals_count = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM orders WHERE run_id = ?", (self.run_id,))
        orders_count = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM fills WHERE run_id = ?", (self.run_id,))
        fills_count = cursor.fetchone()[0]

        # Get equity stats
        cursor.execute(f"""
            SELECT MIN(equity), MAX(equity),
                   (MAX(equity) - MIN(equity)) / MIN(equity) * 100 as return_pct
            FROM equity WHERE run_id = ?
        """, (self.run_id,))
        equity_stats = cursor.fetchone()

        # Get final equity
        cursor.execute(f"""
            SELECT equity, total_pnl, realized_pnl, unrealized_pnl
            FROM equity WHERE run_id = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (self.run_id,))
        final_equity = cursor.fetchone()

        # Get win rate (simplified - based on realized PnL)
        cursor.execute(f"""
            SELECT
                COUNT(CASE WHEN price > 0 THEN 1 END) as winning_trades,
                COUNT(*) as total_trades
            FROM fills WHERE run_id = ?
        """, (self.run_id,))
        trade_stats = cursor.fetchone()

        return {
            "run_id": self.run_id,
            "bars_count": bars_count,
            "signals_count": signals_count,
            "orders_count": orders_count,
            "fills_count": fills_count,
            "equity": {
                "initial": equity_stats[0] if equity_stats else 0,
                "final": final_equity[0] if final_equity else 0,
                "max": equity_stats[1] if equity_stats else 0,
                "return_pct": equity_stats[2] if equity_stats and equity_stats[2] else 0,
                "total_pnl": final_equity[1] if final_equity else 0,
                "realized_pnl": final_equity[2] if final_equity else 0,
                "unrealized_pnl": final_equity[3] if final_equity else 0,
            },
            "trades": {
                "total": trade_stats[1] if trade_stats else 0,
                "winning": trade_stats[0] if trade_stats else 0,
                "win_rate": (trade_stats[0] / max(1, trade_stats[1])) * 100 if trade_stats else 0,
            }
        }

    def export_to_csv(self):
        """Export all tables to CSV files"""
        import csv

        csv_dir = self.run_dir / "data"

        tables = ["bars", "signals", "orders", "fills", "equity", "positions"]

        for table in tables:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT * FROM {table} WHERE run_id = ?", (self.run_id,))

            rows = cursor.fetchall()
            if not rows:
                continue

            # Get column names
            column_names = [description[0] for description in cursor.description]

            # Write to CSV
            csv_path = csv_dir / f"{table}.csv"
            with open(csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(column_names)
                writer.writerows(rows)

            logger.info(f"Exported {len(rows)} rows to {csv_path}")

    def export_to_parquet(self):
        """Export all tables to Parquet files (requires pyarrow)"""
        try:
            import pandas as pd
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            logger.warning("pyarrow not installed, skipping Parquet export")
            return

        parquet_dir = self.run_dir / "data"

        tables = ["bars", "signals", "orders", "fills", "equity", "positions"]

        for table in tables:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT * FROM {table} WHERE run_id = ?", (self.run_id,))

            rows = cursor.fetchall()
            if not rows:
                continue

            # Get column names
            column_names = [description[0] for description in cursor.description]

            # Create DataFrame
            df = pd.DataFrame(rows, columns=column_names)

            # Write to Parquet
            parquet_path = parquet_dir / f"{table}.parquet"
            df.to_parquet(parquet_path, engine='pyarrow', compression='snappy')

            logger.info(f"Exported {len(rows)} rows to {parquet_path}")

    def save_summary(self):
        """Save summary JSON file"""
        summary = self.get_summary_stats()

        # Add metadata
        summary["metadata"] = self.metadata

        summary_path = self.run_dir / "summary.json"
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)

        logger.info(f"Summary saved to {summary_path}")
        return summary

    def compute_hash(self) -> str:
        """
        Compute hash of results for reproducibility verification
        Returns SHA256 hash of fills (deterministic if seeded properly)
        """
        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT symbol, timestamp, side, quantity, price
            FROM fills WHERE run_id = ?
            ORDER BY timestamp, symbol
        """, (self.run_id,))

        rows = cursor.fetchall()

        # Create deterministic string representation
        data_str = "\n".join(["|".join(map(str, row)) for row in rows])

        # Compute hash
        hash_obj = hashlib.sha256(data_str.encode())
        return hash_obj.hexdigest()

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


def create_backtest_persistence(run_id: Optional[str] = None, output_dir: str = "out") -> BacktestPersistence:
    """Factory function to create BacktestPersistence instance"""
    return BacktestPersistence(run_id=run_id, output_dir=output_dir)
