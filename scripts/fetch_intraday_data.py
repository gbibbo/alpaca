#!/usr/bin/env python3
"""
scripts/fetch_intraday_data.py
Detect the Alpaca market-data entitlement (feed + historical depth) THIS account actually has, then
download 1-minute bars for the intraday research line (SPY, QQQ). No assumptions about SIP: the feed
is probed at runtime. Every run records provider / feed / requested range / resolution / row counts
to a sidecar JSON so results are never divorced from their data lineage.

Usage:
    python scripts/fetch_intraday_data.py --detect-only
    python scripts/fetch_intraday_data.py --symbols SPY QQQ --years 2 --out data/intraday

Credentials come from the environment or .env (APCA_API_KEY_ID / APCA_API_SECRET_KEY) and are never
printed. This script only reads market data; it never touches the broker/trading endpoints.
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env():
    """Populate os.environ from .env for APCA_* keys if they are not already set. No printing."""
    for key in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        if os.getenv(key):
            continue
        envf = ROOT / ".env"
        if envf.exists():
            for line in envf.read_text().splitlines():
                line = line.strip()
                if line.startswith(f"{key}=") and not line.startswith("#"):
                    os.environ[key] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break


def _client():
    from alpaca.data.historical import StockHistoricalDataClient
    key, secret = os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise SystemExit("Missing APCA_API_KEY_ID / APCA_API_SECRET_KEY (set them in .env).")
    return StockHistoricalDataClient(key, secret)


def _one_minute_request(symbol, start, end, feed, limit=None):
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame as ATF, TimeFrameUnit
    return StockBarsRequest(symbol_or_symbols=symbol, timeframe=ATF(1, TimeFrameUnit.Minute),
                            start=start, end=end, feed=feed, adjustment="all", limit=limit)


def detect(client, symbol="SPY"):
    """Return {feed, earliest_1m, latest_1m} for the best feed the account can actually use."""
    from alpaca.data.timeframe import TimeFrame as ATF  # noqa: F401  (ensures import path works)
    now = datetime.now(timezone.utc)
    report = {"symbol": symbol, "checked_at": now.isoformat(), "feeds": {}}
    best_feed = None
    for feed in ("sip", "iex"):
        info = {"usable": False}
        try:
            # A tiny recent window (last 3 sessions), asking for a handful of bars.
            req = _one_minute_request(symbol, now - timedelta(days=5), now - timedelta(minutes=20),
                                      feed, limit=5)
            resp = client.get_stock_bars(req)
            rows = resp.data.get(symbol, []) if hasattr(resp, "data") else []
            info["usable"] = len(rows) > 0
            info["sample_rows"] = len(rows)
            if rows:
                info["sample_last_ts"] = rows[-1].timestamp.isoformat()
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"{type(exc).__name__}: {exc}"
        report["feeds"][feed] = info
        if info.get("usable") and best_feed is None:
            best_feed = feed
    report["feed"] = best_feed

    if best_feed:
        # Probe historical depth: walk back year by year until a 1m request returns nothing.
        earliest = None
        for years_back in range(1, 12):
            start = now - timedelta(days=365 * years_back)
            end = start + timedelta(days=3)
            try:
                req = _one_minute_request(symbol, start, end, best_feed, limit=5)
                resp = client.get_stock_bars(req)
                rows = resp.data.get(symbol, []) if hasattr(resp, "data") else []
                if rows:
                    earliest = rows[0].timestamp.isoformat()
                else:
                    break
            except Exception:  # noqa: BLE001
                break
        report["earliest_1m_probe"] = earliest
    return report


def fetch_symbol(client, symbol, start, end, feed, out_dir):
    from alpaca.data.requests import StockBarsRequest  # noqa: F401
    req = _one_minute_request(symbol, start, end, feed)
    resp = client.get_stock_bars(req)
    rows = resp.data.get(symbol, []) if hasattr(resp, "data") else []
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{symbol}_1m_{feed}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume", "trade_count", "vwap"])
        for b in rows:
            w.writerow([b.timestamp.isoformat(), b.open, b.high, b.low, b.close, b.volume,
                        getattr(b, "trade_count", ""), getattr(b, "vwap", "")])
    meta = {"provider": "alpaca", "feed": feed, "symbol": symbol, "resolution": "1Min",
            "adjustment": "all", "requested_start": start.isoformat(), "requested_end": end.isoformat(),
            "rows": len(rows),
            "actual_start": rows[0].timestamp.isoformat() if rows else None,
            "actual_end": rows[-1].timestamp.isoformat() if rows else None,
            "fetched_at": datetime.now(timezone.utc).isoformat(), "csv": str(csv_path.name)}
    (out_dir / f"{symbol}_1m_{feed}.meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    ap.add_argument("--years", type=float, default=2.0, help="how far back to fetch 1m bars")
    ap.add_argument("--out", default="data/intraday")
    ap.add_argument("--detect-only", action="store_true")
    args = ap.parse_args()

    _load_env()
    client = _client()

    det = detect(client, args.symbols[0])
    print(json.dumps({"detection": det}, indent=2))
    if args.detect_only:
        return
    feed = det.get("feed")
    if not feed:
        raise SystemExit("No usable data feed detected; cannot fetch.")

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=int(365 * args.years))
    end = now - timedelta(minutes=20)   # avoid the most recent, possibly-delayed minutes
    out_dir = ROOT / args.out
    metas = []
    for sym in args.symbols:
        meta = fetch_symbol(client, sym, start, end, feed, out_dir)
        metas.append(meta)
        print(json.dumps(meta, indent=2))
    (out_dir / "fetch_manifest.json").write_text(json.dumps(
        {"detection": det, "fetches": metas, "feed": feed}, indent=2))


if __name__ == "__main__":
    main()
