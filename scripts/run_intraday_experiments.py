#!/usr/bin/env python3
"""
scripts/run_intraday_experiments.py
Run the preregistered intraday experiments (spec item 11) on real SPY/QQQ 1-minute data pulled by
scripts/fetch_intraday_data.py. Resolutions are never mixed: the single 1m SIP source is RTH-
filtered, cleaned to COMPLETE sessions (partial first/last sessions discarded via the market
calendar, not inferred from the last available bar), reduced to the sessions that are complete for
BOTH symbols, and resampled session-aligned to each strategy's own timeframe. For every
(strategy, symbol) we record gross AND net metrics, the full intraday block, robustness across
contiguous time sub-windows, and a descriptive cost-sensitivity sweep.

    Batch A  extreme_reversal_1m                     1m   SPY, QQQ
    Batch B  opening_range_breakout_5m               5m   SPY, QQQ
    Batch C  market_intraday_momentum_30m            30m  SPY, QQQ   (long-only)
    Batch C  market_intraday_momentum_30m_long_short 30m  SPY, QQQ   (faithful Gao long/short)
    Batch D  smart_technical (1m, FULL window), intraday_momentum_5m (5m),
             intraday_momentum_15m (15m, EXPLORATORY), hourly_trend (1h),
             hourly_trend_intraday (1h, no-overnight ablation)         SPY, QQQ

Aggregate results (no raw bars) are written to data/intraday_results/ for committing; the raw SIP
CSVs stay gitignored under data/intraday/.

    python scripts/run_intraday_experiments.py --data data/intraday --out data/intraday_results
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.models import Bar, TimeFrame
from lib.timeframes import in_regular_session, parse_timeframe
from lib.resampler import BarResampler
from lib.backtest import run_backtest, ResearchConfig, cost_sensitivity, COST_SCENARIOS
from lib.session_filter import classify_sessions, session_date

# (batch, strategy, timeframe, opts). opts.subwindows toggles the 3-way time-robustness split.
# No strategy is capped any more: smart_technical runs the FULL window (its MACD is now O(len)).
EXPERIMENTS = [
    ("A", "extreme_reversal_1m", "1m", {"subwindows": True}),
    ("B", "opening_range_breakout_5m", "5m", {"subwindows": True}),
    ("C", "market_intraday_momentum_30m", "30m", {"subwindows": True}),
    ("C", "market_intraday_momentum_30m_long_short", "30m", {"subwindows": True}),
    ("D", "smart_technical", "1m", {"subwindows": True}),
    ("D", "intraday_momentum_5m", "5m", {"subwindows": True}),
    ("D", "intraday_momentum_15m", "15m", {"subwindows": True}),
    ("D", "hourly_trend", "1h", {"subwindows": True}),
    ("D", "hourly_trend_intraday", "1h", {"subwindows": True}),
]
SYMBOLS = ["SPY", "QQQ"]

# Baseline frictions used for the headline net figure (SPY/QQQ are deep, liquid ETFs).
BASE_COMMISSION_BPS = 0.0     # Alpaca charges no commission on US equities
BASE_SLIPPAGE_BPS = 0.5
BASE_SPREAD_BPS = 1.0


def load_rth_1m(csv_path, symbol):
    """Load 1m bars from CSV and keep only regular-session (RTH) bars -- SIP includes extended
    hours, which must be excluded from intraday research."""
    out = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"])
            if not in_regular_session(ts):
                continue
            out.append(Bar(symbol=symbol, timestamp=ts, timeframe=TimeFrame.MINUTE,
                           open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                           close=float(row["close"]), volume=float(row["volume"] or 0)))
    out.sort(key=lambda b: b.timestamp)
    return out


def to_timeframe(bars_1m, tf):
    """Session-aligned resample of RTH 1m bars to tf (1m returned as-is)."""
    if tf == TimeFrame.MINUTE:
        return bars_1m
    r = BarResampler(targets=[tf], session_aligned=True)
    out = []
    for b in bars_1m:
        out.extend(r.add(b))
    out.extend(r.flush())
    out.sort(key=lambda b: b.timestamp)
    return out


def _cfg(strategy, commission, slippage, spread):
    return ResearchConfig(strategies=[strategy], initial_cash=100_000,
                          max_volume_participation=0.05, commission_bps=commission,
                          slippage_bps=slippage, spread_bps=spread,
                          stop_loss_pct=None, take_profit_pct=None)


def _headline(acc):
    m = acc["metrics"]
    intr = m.get("intraday")
    block = {
        "kind": acc.get("kind", "per_symbol"),
        "gross_return_pct": m["gross_return_pct"], "net_return_pct": m["net_return_pct"],
        "total_costs": m["costs"]["total"], "cost_drag_pct": m["costs"]["cost_drag_pct"],
        "gross_edge_kept_pct": m["costs"]["gross_edge_kept_pct"],
        "max_drawdown_pct": m["max_drawdown_pct"], "sharpe_daily": m["sharpe"],
        "avg_exposure_pct": m["avg_exposure_pct"], "turnover": m["turnover"],
        "win_rate_pct": m["trades"].get("win_rate"), "profit_factor": m["trades"].get("profit_factor"),
        "n_closed_trades": m["trades"].get("total"),
    }
    if intr:
        block["intraday"] = intr
    if "session_audit" in acc:
        block["session_audit"] = acc["session_audit"]
    return block


def _subwindows(bars, k=3):
    """Split bars into k contiguous, session-clean chunks (split on session boundaries)."""
    if not bars:
        return []
    n = len(bars)
    cuts = [int(n * j / k) for j in range(1, k)]
    chunks, start = [], 0
    for c in cuts:
        # advance c to the next session boundary so a session is never split across windows
        while c < n and c > 0 and bars[c].timestamp.date() == bars[c - 1].timestamp.date():
            c += 1
        chunks.append(bars[start:c]); start = c
    chunks.append(bars[start:])
    return [ch for ch in chunks if ch]


def run_one(strategy, tf_str, bars_1m, opts=None):
    opts = opts or {}
    tf = parse_timeframe(tf_str)
    bars = to_timeframe(bars_1m, tf)
    if not bars:
        return {"error": "no bars after resample", "timeframe": tf_str}
    cfg = _cfg(strategy, BASE_COMMISSION_BPS, BASE_SLIPPAGE_BPS, BASE_SPREAD_BPS)
    res = run_backtest(bars, cfg)
    acc = res["accounts"][strategy]
    out = {"timeframe": tf_str, "n_bars": len(bars),
           "data_start": bars[0].timestamp.isoformat(), "data_end": bars[-1].timestamp.isoformat(),
           "headline": _headline(acc)}

    # Robustness across contiguous time sub-windows (item 12: not driven by 1-2 days/windows).
    subs = []
    if opts.get("subwindows", True):
        for j, chunk in enumerate(_subwindows(bars, 3)):
            r = run_backtest(chunk, cfg)["accounts"][strategy]["metrics"]
            subs.append({"window": j, "start": chunk[0].timestamp.isoformat(),
                         "end": chunk[-1].timestamp.isoformat(),
                         "net_return_pct": r["net_return_pct"], "gross_return_pct": r["gross_return_pct"],
                         "sharpe_daily": r["sharpe"], "trades": r["trades"].get("total")})
    out["subwindows"] = subs

    # Descriptive cost sensitivity (robustness, NOT optimization): predefined scenarios.
    try:
        out["cost_sensitivity"] = cost_sensitivity(bars, cfg, COST_SCENARIOS)
    except Exception as exc:  # noqa: BLE001
        out["cost_sensitivity"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def clean_to_common_sessions(sources):
    """Given {symbol: [1m RTH bars]}, discard partial sessions per symbol (calendar-derived close)
    and keep only the sessions COMPLETE for EVERY symbol, so all strategies and both symbols run on
    exactly the same sessions. Returns (filtered_sources, accounting)."""
    per_symbol_complete = {}
    audits = {}
    for sym, bars in sources.items():
        audit = classify_sessions(bars, TimeFrame.MINUTE)
        audits[sym] = audit
        per_symbol_complete[sym] = {d for (s, d) in audit["complete_keys"]}
    common = set.intersection(*per_symbol_complete.values()) if per_symbol_complete else set()

    filtered = {sym: [b for b in bars if session_date(b) in common] for sym, bars in sources.items()}
    accounting = {
        "timeframe_of_completeness": "1Min",
        "close_source": "market_calendar (lib.market_calendar.session_bounds); never inferred from "
                        "the last available bar",
        "common_complete_sessions": len(common),
        "common_session_first": min(common).isoformat() if common else None,
        "common_session_last": max(common).isoformat() if common else None,
        "per_symbol": {},
    }
    for sym in sources:
        t = audits[sym]["totals"]
        disc = audits[sym]["per_symbol"].get(sym, {}).get("discarded", [])
        complete_dates = per_symbol_complete[sym]
        accounting["per_symbol"][sym] = {
            "sessions_total": t["total"], "complete": t["complete"], "incomplete": t["incomplete"],
            "discarded_incomplete": [list(x) for x in disc],
            "complete_but_not_common": sorted(d.isoformat() for d in (complete_dates - common)),
        }
    return filtered, accounting


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/intraday")
    ap.add_argument("--out", default="data/intraday_results")
    ap.add_argument("--feed", default="sip")
    args = ap.parse_args()

    data_dir = ROOT / args.data
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((data_dir / "fetch_manifest.json").read_text()) \
        if (data_dir / "fetch_manifest.json").exists() else {}

    # Load + RTH-filter each symbol's 1m source once.
    raw = {}
    for sym in SYMBOLS:
        csv_path = data_dir / f"{sym}_1m_{args.feed}.csv"
        if not csv_path.exists():
            print(f"!! missing {csv_path}; run fetch_intraday_data.py first")
            continue
        bars = load_rth_1m(csv_path, sym)
        raw[sym] = bars
        print(f"{sym}: {len(bars)} RTH 1m bars {bars[0].timestamp.date()}..{bars[-1].timestamp.date()}")

    # Clean to COMPLETE sessions common to every symbol (spec block 1 + 6).
    sources, accounting = clean_to_common_sessions(raw)
    print(f"common complete sessions: {accounting['common_complete_sessions']} "
          f"({accounting['common_session_first']}..{accounting['common_session_last']})")
    for sym, a in accounting["per_symbol"].items():
        print(f"  {sym}: total={a['sessions_total']} complete={a['complete']} "
              f"incomplete={a['incomplete']} not_common={len(a['complete_but_not_common'])}")

    results = {"generated_at": datetime.utcnow().isoformat() + "Z",
               "data_lineage": {"provider": "alpaca", "feed": args.feed,
                                "resolution_source": "1Min", "rth_only": True,
                                "fetch_manifest": manifest.get("fetches", [])},
               "session_accounting": accounting,
               "costs_headline": {"commission_bps": BASE_COMMISSION_BPS,
                                  "slippage_bps": BASE_SLIPPAGE_BPS, "spread_bps": BASE_SPREAD_BPS},
               "batches": {}}

    for batch, strategy, tf_str, opts in EXPERIMENTS:
        for sym in SYMBOLS:
            if sym not in sources:
                continue
            key = f"{batch}:{strategy}:{sym}"
            print(f"running {key} ...", flush=True)
            try:
                results["batches"][key] = run_one(strategy, tf_str, sources[sym], opts)
                results["batches"][key]["symbol"] = sym
                results["batches"][key]["strategy"] = strategy
                results["batches"][key]["batch"] = batch
            except Exception as exc:  # noqa: BLE001
                results["batches"][key] = {"error": f"{type(exc).__name__}: {exc}",
                                           "symbol": sym, "strategy": strategy, "batch": batch}
                print(f"   ERROR {key}: {exc}")

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"intraday_experiments_{stamp}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    (out_dir / "latest.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
