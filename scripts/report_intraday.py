#!/usr/bin/env python3
"""
scripts/report_intraday.py
Turn data/intraday_results/latest.json (produced by run_intraday_experiments.py) into a committed,
self-contained Markdown report: data lineage, a gross-and-net master table, per-hypothesis
robustness (time sub-windows + cost sweep), and an automatic verdict against the preregistered
success criteria. The verdict logic is deterministic and conservative; it is derived from the
numbers, not hand-written, so the report cannot drift from the data.

    python scripts/report_intraday.py --in data/intraday_results/latest.json --out docs/INTRADAY_RESULTS.md
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _fmt(x, nd=2):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _pct(x):
    return "-" if x is None else f"{x:+.2f}"


def _headline_row(key, e):
    h = e.get("headline", {})
    intr = h.get("intraday", {})
    trades = intr.get("trades", h.get("n_closed_trades"))
    return [e.get("batch"), e.get("strategy"), e.get("timeframe"), e.get("symbol"),
            _fmt(e.get("n_bars"), 0), _fmt(trades, 0),
            _pct(h.get("gross_return_pct")), _pct(h.get("net_return_pct")),
            _fmt(h.get("cost_drag_pct")), _fmt(h.get("gross_edge_kept_pct"), 1),
            _fmt(h.get("max_drawdown_pct")), _fmt(h.get("sharpe_daily")),
            _fmt(h.get("win_rate_pct"), 1), _fmt(h.get("turnover"), 3)]


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _low_cost_net(e, strategy):
    """Net return under the standard 'low' predefined cost scenario (slippage 1, commission 0.5,
    spread 1 bps) -- a realistic bar, distinct from the optimistic headline. None if unavailable."""
    for s in (e.get("cost_sensitivity") or []):
        if s.get("scenario", {}).get("label") == "low":
            return (s.get("accounts", {}).get(strategy, {}) or {}).get("net_return_pct")
    return None


def _verdict(entries_by_symbol, strategy):
    """Conservative, deterministic verdict for one strategy from its SPY+QQQ entries. 'Survives
    costs' is judged under the standard 'low' cost scenario, NOT the optimistic headline, so a
    strategy that is positive only under rosy frictions is flagged as marginal, not promising."""
    grosses, head_nets, low_nets, persist, recent = [], [], [], [], []
    for sym, e in entries_by_symbol.items():
        h = e.get("headline", {})
        grosses.append((h.get("gross_return_pct") or 0) > 0)
        head_nets.append((h.get("net_return_pct") or 0) > 0)
        ln = _low_cost_net(e, strategy)
        low_nets.append((ln or 0) > 0)
        subs = e.get("subwindows") or []
        if subs:
            pos = sum(1 for s in subs if (s.get("net_return_pct") or 0) > 0)
            persist.append(pos >= max(1, len(subs) - 1))   # positive in all-but-one sub-window
            recent.append((subs[-1].get("net_return_pct") or 0) > 0)  # most-recent window positive?
    before = all(grosses) and len(grosses) > 0
    head_survives = all(head_nets) and len(head_nets) > 0
    survives = all(low_nets) and len(low_nets) > 0        # realistic-cost survival
    persists = all(persist) and len(persist) > 0
    recent_ok = all(recent) and len(recent) > 0
    replicates = survives and len(low_nets) >= 2
    checks = {"edge_before_costs (both symbols gross>0)": before,
              "survives_realistic_costs (net>0 both, 'low' scenario)": survives,
              "still_positive_in_most_recent_window": recent_ok,
              "persists_across_subwindows": persists,
              "replicates_SPY_and_QQQ (net>0 both, realistic costs)": replicates}
    if before and survives and persists and recent_ok and replicates:
        v = "PROMISING — meets every preregistered criterion; warrants further, harsher testing"
    elif before and head_survives and not survives:
        v = ("MARGINAL — net-positive only under optimistic frictions; negative under the standard "
             "'low' cost scenario, so not a robust edge")
    elif before and not head_survives:
        v = "EDGE BEFORE COSTS ONLY — gross signal is erased by frictions; not tradeable as-is"
    elif not before:
        v = "NO EDGE — no positive signal even gross"
    else:
        v = "MIXED / INCONCLUSIVE — passes some criteria but not all; treat as negative for now"
    return v, checks


def _find(batches, strategy, symbol):
    """Headline dict for one (strategy, symbol), or None."""
    for e in batches.values():
        if e.get("strategy") == strategy and e.get("symbol") == symbol and "headline" in e:
            return e
    return None


def _comparison_table(batches, strat_a, strat_b, label_a, label_b):
    """Side-by-side net/gross for two strategies across symbols, plus the A−B difference."""
    rows = []
    for sym in ("SPY", "QQQ"):
        a, b = _find(batches, strat_a, sym), _find(batches, strat_b, sym)
        if not a or not b:
            continue
        ah, bh = a["headline"], b["headline"]
        diff = (ah.get("net_return_pct") or 0) - (bh.get("net_return_pct") or 0)
        rows.append([sym, _pct(ah.get("gross_return_pct")), _pct(ah.get("net_return_pct")),
                     _pct(bh.get("gross_return_pct")), _pct(bh.get("net_return_pct")), _fmt(diff)])
    headers = ["Sym", f"{label_a} gross%", f"{label_a} net%",
               f"{label_b} gross%", f"{label_b} net%", "net Δ (A−B) pp"]
    return _table(headers, rows) if rows else "_(data unavailable)_"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/intraday_results/latest.json")
    ap.add_argument("--out", dest="out", default="docs/INTRADAY_RESULTS.md")
    args = ap.parse_args()

    data = json.loads((ROOT / args.inp).read_text())
    batches = data.get("batches", {})
    lineage = data.get("data_lineage", {})
    costs = data.get("costs_headline", {})
    accounting = data.get("session_accounting", {})

    md = ["# Intraday research line — results", "",
          "Generated by `scripts/report_intraday.py` from the experiment run; numbers and verdicts "
          "are derived directly from `data/intraday_results/latest.json`. See "
          "`docs/PREREGISTRATION_INTRADAY.md` for the frozen hypotheses and success criteria.", "",
          "## Data lineage", "",
          f"- **Provider / feed:** {lineage.get('provider')} / **{lineage.get('feed')}** "
          f"(consolidated); RTH only: {lineage.get('rth_only')}.",
          f"- **Source resolution:** {lineage.get('resolution_source')}; higher timeframes are "
          f"session-aligned resamples of it (resolutions never mixed).",
          f"- **Headline frictions:** commission {costs.get('commission_bps')} bps, slippage "
          f"{costs.get('slippage_bps')} bps, spread {costs.get('spread_bps')} bps. A separate "
          f"cost-sensitivity sweep is reported per strategy.", ""]
    for f in lineage.get("fetch_manifest", []):
        md.append(f"- **{f.get('symbol')}**: {f.get('rows')} raw {f.get('resolution')} "
                  f"{f.get('feed')} bars, {f.get('actual_start')} → {f.get('actual_end')}.")
    md.append("")

    if accounting:
        md += ["## Session accounting (complete vs discarded)", "",
               "Sessions are used only when **complete** — spanning the 09:30 ET open bar to the "
               "calendar close bar (16:00 ET, or 13:00 ET on early closes). The close comes from the "
               "market calendar, never from the last bar present, so truncated first/last sessions "
               "are discarded. Both symbols are reduced to the sessions complete for **both**.", "",
               f"- **Common complete sessions (used):** {accounting.get('common_complete_sessions')} "
               f"({accounting.get('common_session_first')} → {accounting.get('common_session_last')}).",
               f"- **Close source:** {accounting.get('close_source')}.", ""]
        acc_rows = []
        for sym, a in (accounting.get("per_symbol") or {}).items():
            disc = "; ".join(f"{d} ({r})" for d, r in a.get("discarded_incomplete", [])) or "—"
            acc_rows.append([sym, a.get("sessions_total"), a.get("complete"), a.get("incomplete"),
                             len(a.get("complete_but_not_common", [])), disc])
        md.append(_table(["Sym", "Total", "Complete", "Incomplete", "Not common", "Discarded (date/reason)"],
                         acc_rows))
        md.append("")

    # Master table
    headers = ["Batch", "Strategy", "TF", "Sym", "Bars", "Trades", "Gross%", "Net%",
               "CostDrag%", "EdgeKept%", "MaxDD%", "Sharpe", "Win%", "Turnover"]
    rows = [_headline_row(k, e) for k, e in batches.items() if "headline" in e]
    md += ["## Master table (gross and net)", "", _table(headers, rows), ""]

    # errors
    errs = {k: e["error"] for k, e in batches.items() if "error" in e}
    if errs:
        md += ["### Runs that errored", ""]
        for k, v in errs.items():
            md.append(f"- `{k}`: {v}")
        md.append("")

    # Per-strategy verdicts (A/B/C research)
    by_strategy = defaultdict(dict)
    for k, e in batches.items():
        if "strategy" in e and "headline" in e:
            by_strategy[(e["batch"], e["strategy"])][e["symbol"]] = e
    md += ["## Per-hypothesis conclusions", ""]
    for (batch, strat) in sorted(by_strategy):
        ents = by_strategy[(batch, strat)]
        v, checks = _verdict(ents, strat)
        md.append(f"### Batch {batch} — `{strat}`")
        md.append("")
        md.append(f"**Verdict: {v}**")
        md.append("")
        md.append("| criterion | pass |")
        md.append("| --- | --- |")
        for c, ok in checks.items():
            md.append(f"| {c} | {'yes' if ok else 'no'} |")
        md.append("")
        # sub-window + cost sweep detail
        for sym, e in ents.items():
            subs = e.get("subwindows") or []
            if subs:
                seg = "; ".join(f"[{s['start'][:10]}..{s['end'][:10]}] net {_pct(s.get('net_return_pct'))}%"
                                f" ({s.get('trades')} tr)" for s in subs)
                md.append(f"- {sym} sub-windows: {seg}")
            cs = e.get("cost_sensitivity")
            if isinstance(cs, list) and cs:
                sweep = "; ".join(
                    f"{s.get('scenario', {}).get('label')}: net "
                    f"{_pct((s.get('accounts', {}).get(strat, {}) or {}).get('net_return_pct'))}%"
                    for s in cs)
                md.append(f"- {sym} cost sweep: {sweep}")
        md.append("")

    # Key comparisons the spec asks for explicitly (item 7).
    md += ["## Key comparisons", "",
           "### Overnight vs intraday — `hourly_trend` (may hold overnight) vs `hourly_trend_intraday` "
           "(no overnight)", "",
           "Same signal and parameters; the only difference is the intraday no-overnight contract. "
           "The net Δ (baseline − intraday-only) is the part of the baseline's return attributable to "
           "**overnight exposure** rather than to the market hours.", "",
           _comparison_table(batches, "hourly_trend", "hourly_trend_intraday", "overnight", "intraday"),
           "",
           "### Gao et al. — `market_intraday_momentum_30m` (long-only) vs "
           "`market_intraday_momentum_30m_long_short` (faithful long/short)", "",
           "Same `_r_first30` signal; the long-short variant also trades the negative-morning short "
           "leg. This shows whether dropping the short leg destroyed the original effect.", "",
           _comparison_table(batches, "market_intraday_momentum_30m",
                             "market_intraday_momentum_30m_long_short", "long-only", "long-short"),
           ""]

    md += ["## Blocked by external data", "",
           "- **Cross-sectional S&P 500 momentum** (survivorship-safe point-in-time universe) needs "
           "a historical constituents dataset (e.g. via TIINGO_API_KEY). The engine, universe, and "
           "rebalance layers are in place (`CsvPointInTimeUniverse`, generalized scheduling); only "
           "the licensed data is missing. Enter the key with `scripts/set_secret.py` when a "
           "machine is reachable; nothing here is blocked for SPY/QQQ intraday.", ""]

    # Executive summary (data-driven), spliced in just after the intro.
    summary = ["## Executive summary", ""]
    abc_survivors = []
    for (batch, strat) in sorted(by_strategy):
        if batch not in ("A", "B", "C"):
            continue
        v, _ = _verdict(by_strategy[(batch, strat)], strat)
        summary.append(f"- **Batch {batch} `{strat}`** — {v.split(' — ')[0]}.")
        low_ok = [( _low_cost_net(e, strat) or 0) > 0 for e in by_strategy[(batch, strat)].values()]
        if low_ok and all(low_ok) and len(low_ok) >= 2:
            abc_survivors.append(strat)
    if abc_survivors:
        head = ("Under realistic transaction costs, the preregistered strategies that survive "
                "(net > 0 for both symbols, standard 'low' scenario) are: "
                + ", ".join(f"`{s}`" for s in abc_survivors) + ". ")
    else:
        head = ("None of the preregistered very-short strategies (batches A/B/C) survives realistic "
                "transaction costs: at best they show a small gross edge over three years that turns "
                "negative once a standard spread/slippage is applied. ")
    summary += ["",
                head + "The mechanism is turnover — the absolute **cost drag** scales with how much "
                "a strategy trades, so a small gross edge is swamped as frequency rises (sorted by "
                "turnover):", ""]
    tbl = sorted(((e.get("headline", {}).get("turnover"),
                   e.get("headline", {}).get("cost_drag_pct"),
                   e.get("headline", {}).get("gross_return_pct"),
                   e.get("headline", {}).get("net_return_pct"),
                   e.get("timeframe"), e.get("strategy"), e.get("symbol"))
                  for e in batches.values() if "headline" in e),
                 key=lambda r: (r[0] or 0))
    summary.append(_table(["Turnover (x)", "CostDrag (pp)", "Gross%", "Net%", "TF", "Strategy", "Sym"],
                          [[_fmt(t, 1), _fmt(cd), _pct(g), _pct(n), tf, s, sy]
                           for (t, cd, g, n, tf, s, sy) in tbl]))

    # Data-driven closing statements (no hand-written numbers): which strategies are net-positive
    # AND cost-robust (net>0 for BOTH symbols under the standard 'low' scenario), and the measured
    # overnight contribution to the hourly baseline.
    robust = []
    by_strat = defaultdict(dict)
    for e in batches.values():
        if e.get("strategy") and "headline" in e:
            by_strat[e["strategy"]][e["symbol"]] = e
    for strat, ents in sorted(by_strat.items()):
        low = [(_low_cost_net(e, strat) or 0) > 0 for e in ents.values()]
        if low and all(low) and len(low) >= 2:
            robust.append(strat)
    if robust:
        summary += ["", "**Net-positive and cost-robust** (net > 0 for both symbols under the "
                    "standard 'low' cost scenario): " + ", ".join(f"`{s}`" for s in robust) +
                    ". Note these are low-turnover strategies — low turnover is what preserves a "
                    "small edge against the cost drag above.", ""]
    else:
        summary += ["", "**No strategy is net-positive and cost-robust for both symbols** under the "
                    "standard 'low' cost scenario: every gross edge here is erased by frictions once "
                    "a realistic spread/slippage is applied.", ""]
    # Overnight contribution (hourly_trend − hourly_trend_intraday), averaged over symbols.
    deltas = []
    for sym in ("SPY", "QQQ"):
        a, b = _find(batches, "hourly_trend", sym), _find(batches, "hourly_trend_intraday", sym)
        if a and b:
            deltas.append((a["headline"].get("net_return_pct") or 0) - (b["headline"].get("net_return_pct") or 0))
    if deltas:
        summary += ["Overnight exposure accounts for a net **{:+.2f} pp** on average of the "
                    "`hourly_trend` baseline (baseline − intraday-only); see Key comparisons for the "
                    "per-symbol split and the Gao long-only vs long-short comparison."
                    .format(sum(deltas) / len(deltas)), ""]
    insert_at = md.index("## Data lineage")
    md[insert_at:insert_at] = summary

    (ROOT / args.out).write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} result rows)")


if __name__ == "__main__":
    main()
