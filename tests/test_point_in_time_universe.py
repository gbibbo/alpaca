#!/usr/bin/env python3
"""
tests/test_point_in_time_universe.py
load_csv accepts dotted tickers (BRK.B) safely, CsvPointInTimeUniverse answers members(as_of)
from a composition file, and the portfolio backtest only ever holds names that were index
members on each date (no survivorship bias), with the membership frozen by SHA256.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, datetime, timedelta, timezone

import pytest

from lib.models import Bar, TimeFrame
from lib.backtest import load_csv, run_backtest, ResearchConfig
from lib.portfolio_strategy import CsvPointInTimeUniverse, StaticUniverse


def _write_price_csv(path, closes, start=date(2022, 1, 3)):
    d = start
    with path.open("w", newline="", encoding="utf-8") as f:
        f.write("timestamp,open,high,low,close,volume\n")
        for c in closes:
            while d.weekday() >= 5:
                d += timedelta(days=1)
            f.write(f"{d.isoformat()}T00:00:00Z,{c},{c*1.01},{c*0.99},{c},1000000\n")
            d += timedelta(days=1)


class TestLoadCsvTickers:
    def test_accepts_dotted_ticker(self, tmp_path):
        _write_price_csv(tmp_path / "BRK.B.csv", [100 + i for i in range(5)])
        bars = load_csv(str(tmp_path), ["BRK.B"], "1Day")
        assert bars and all(b.symbol == "BRK.B" for b in bars)

    def test_rejects_path_traversal(self, tmp_path):
        with pytest.raises(ValueError):
            load_csv(str(tmp_path), ["../secret"], "1Day")
        with pytest.raises(ValueError):
            load_csv(str(tmp_path), ["a/b"], "1Day")


class TestCsvPointInTimeUniverse:
    def test_long_format_membership_and_forward_fill(self, tmp_path):
        comp = tmp_path / "composition.csv"
        comp.write_text(
            "date,symbol\n"
            "2016-01-04,OLD\n2016-01-04,STAY\n"      # OLD + STAY at the start
            "2016-07-01,STAY\n2016-07-01,NEW\n",     # OLD leaves, NEW joins
            encoding="utf-8")
        u = CsvPointInTimeUniverse(str(comp))
        assert set(u.members(datetime(2016, 3, 1, tzinfo=timezone.utc))) == {"OLD", "STAY"}
        assert set(u.members(datetime(2016, 8, 1, tzinfo=timezone.utc))) == {"STAY", "NEW"}
        assert u.members(datetime(2015, 1, 1, tzinfo=timezone.utc)) == []   # before first date
        assert set(u.members(datetime(2016, 7, 1, tzinfo=timezone.utc))) == {"STAY", "NEW"}  # on the change date
        assert u.all_symbols == ["NEW", "OLD", "STAY"]

    def test_wide_format(self, tmp_path):
        comp = tmp_path / "wide.csv"
        comp.write_text("date,symbols\n2016-01-04,AAA;BBB CCC\n", encoding="utf-8")
        u = CsvPointInTimeUniverse(str(comp))
        assert set(u.members(datetime(2016, 2, 1, tzinfo=timezone.utc))) == {"AAA", "BBB", "CCC"}

    def test_rejects_missing_columns(self, tmp_path):
        bad = tmp_path / "bad.csv"
        bad.write_text("day,ticker\n2016-01-04,AAA\n", encoding="utf-8")
        with pytest.raises(ValueError):
            CsvPointInTimeUniverse(str(bad))


class TestPortfolioRespectsMembership:
    def _setup(self, tmp_path):
        # Three symbols, ~2y of daily bars. LATE only becomes an index member near the end.
        start = date(2022, 1, 3)
        drifts = {"AAA": 0.0010, "BBB": 0.0005, "LATE": 0.0030}   # LATE has the strongest momentum
        n = 330
        for s, g in drifts.items():
            _write_price_csv(tmp_path / f"{s}.csv", [100 * (1 + g) ** i for i in range(n)], start)
        # LATE joins the index only in the final month; before that only AAA, BBB are eligible.
        comp = tmp_path / "composition.csv"
        lines = ["date,symbol", "2022-01-03,AAA", "2022-01-03,BBB"]
        join = start + timedelta(days=300)
        lines += [f"{join.isoformat()},AAA", f"{join.isoformat()},BBB", f"{join.isoformat()},LATE"]
        comp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return comp, join

    def test_late_joiner_only_held_after_membership(self, tmp_path):
        comp, join = self._setup(tmp_path)
        bars = load_csv(str(tmp_path), ["AAA", "BBB", "LATE"], "1Day")
        cfg = ResearchConfig(strategies=["xsmom_12_1_long_only"], initial_cash=1_000_000,
                             max_volume_participation=1.0, commission_bps=0, slippage_bps=0,
                             universe_csv=str(comp))
        r = run_backtest(bars, cfg)
        acc = r["accounts"]["xsmom_12_1_long_only"]
        assert "universe_sha256" in r                    # membership frozen for reproducibility
        # Despite LATE having the best momentum the whole time, it can only be selected once it is
        # an index member: every holding before the join date is AAA or BBB, never LATE.
        for rb in acc["rebalances"]:
            held_late = "LATE" in rb["holdings"]
            if datetime.fromisoformat(rb["timestamp"]).date() < join:
                assert not held_late, rb
        assert any("LATE" in rb["holdings"] for rb in acc["rebalances"]), "LATE never became eligible"

    def test_deterministic_with_point_in_time_universe(self, tmp_path):
        comp, _ = self._setup(tmp_path)
        bars = load_csv(str(tmp_path), ["AAA", "BBB", "LATE"], "1Day")
        cfg = ResearchConfig(strategies=["xsmom_12_1_long_only"], initial_cash=1_000_000,
                             max_volume_participation=1.0, universe_csv=str(comp))
        assert run_backtest(bars, cfg) == run_backtest(bars, cfg)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
