#!/usr/bin/env python3
"""
lib/resampler.py
Aggregate 1-minute bars into higher timeframes (5m, 1h) on the fly.

A derived bar is emitted only when its bucket is complete, i.e. when the first 1m bar of the
*next* bucket arrives (or on flush()). The emitted bar's timestamp is the bucket start, matching
Alpaca's convention. Only TimeFrame.MINUTE input is consumed; anything else is ignored so the
same stream can carry 1m and 1d bars safely.
"""

import logging
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from lib.models import Bar, TimeFrame
from lib.timeframes import bucket_start, parse_timeframe

logger = logging.getLogger(__name__)


class BarResampler:
    def __init__(self, targets: Iterable = (TimeFrame.FIVE_MINUTE, TimeFrame.HOUR)):
        self.targets: List[TimeFrame] = []
        for t in targets:
            tf = parse_timeframe(t)
            if tf == TimeFrame.MINUTE:
                continue  # nothing to derive
            if tf == TimeFrame.DAY:
                logger.warning("Daily bars should come from Alpaca directly (session-aligned); skipping 1d resample target")
                continue
            self.targets.append(tf)
        # (symbol, timeframe) -> partial bucket
        self._open: Dict[Tuple[str, TimeFrame], dict] = {}
        self._last_input_ts: Dict[str, datetime] = {}
        self.bars_in = 0
        self.bars_out = 0
        self.dropped_out_of_order = 0

    def add(self, bar: Bar) -> List[Bar]:
        """Feed one 1m bar; returns any completed higher-timeframe bars."""
        if bar.timeframe != TimeFrame.MINUTE or not self.targets:
            return []

        last = self._last_input_ts.get(bar.symbol)
        if last is not None and bar.timestamp <= last:
            # Duplicate or out-of-order minute (live loop re-fetching the same last bar)
            self.dropped_out_of_order += 1
            return []
        self._last_input_ts[bar.symbol] = bar.timestamp
        self.bars_in += 1

        completed: List[Bar] = []
        for tf in self.targets:
            key = (bar.symbol, tf)
            start = bucket_start(bar.timestamp, tf)
            current = self._open.get(key)

            if current is not None and current["start"] != start:
                completed.append(self._close(key))
                current = None

            if current is None:
                self._open[key] = {
                    "start": start,
                    "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
                    "volume": bar.volume, "count": 1,
                }
            else:
                current["high"] = max(current["high"], bar.high)
                current["low"] = min(current["low"], bar.low)
                current["close"] = bar.close
                current["volume"] += bar.volume
                current["count"] += 1

        return completed

    def flush(self, symbol: Optional[str] = None) -> List[Bar]:
        """Emit all open (partial) buckets. Use at the end of a historical replay."""
        out = []
        for key in list(self._open.keys()):
            if symbol is None or key[0] == symbol:
                out.append(self._close(key))
        return out

    def _close(self, key: Tuple[str, TimeFrame]) -> Bar:
        symbol, tf = key
        b = self._open.pop(key)
        self.bars_out += 1
        return Bar(
            symbol=symbol,
            timestamp=b["start"],
            open=b["open"], high=b["high"], low=b["low"], close=b["close"],
            volume=b["volume"],
            timeframe=tf,
        )

    def get_stats(self) -> dict:
        return {
            "targets": [t.value for t in self.targets],
            "bars_in": self.bars_in,
            "bars_out": self.bars_out,
            "open_buckets": len(self._open),
            "dropped_out_of_order": self.dropped_out_of_order,
        }
