#!/usr/bin/env python3
"""
stalled_trend_vetoed_trades_study.py — the trades the stalled veto removes: were they good or bad?
================================================================================
The user, 29 Sep 2026: "so how to make both [in-sample and out-of-sample] on positive side" ->
answered with three avenues, the user: "yes dig into all three." This is avenue #1: don't just
retune the threshold, look at what the veto actually did to the trades in each period.

stalled_trend_filter_study.py (and its threshold sweep) already showed the pattern: at every
threshold the veto helped in-sample and hurt out-of-sample. This isolates exactly the trades that
difference comes from - the ones today's real gate takes that the veto (at 1.0 ATR, today's live
TREND_MIN_DISPLACEMENT_ATR) would refuse - and prices THEM ALONE, in each period, so "helped
in-sample / hurt held-out" becomes a concrete answer (were the vetoed trades winners or losers)
rather than just two totals.

    python3 stalled_trend_vetoed_trades_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res
import stalled_trend_filter_study as sts

INDICES = ps.INDICES
SPLIT = ps.SPLIT
THRESHOLD = 1.0     # today's live TREND_MIN_DISPLACEMENT_ATR


def period(rows, lo, hi):
    """Priced rows (pro_study.price()'s own dicts) whose entry falls in [lo, hi) - half-open,
    matching pro_study.SPLIT's own convention (in-sample is < SPLIT, out-of-sample is >= it)."""
    return [r for r in rows if lo <= r["when"] < hi]


def summarise(rows):
    """Count, total, win rate and average win/loss for a set of priced rows, as numbers - a
    caller formats them for display. A trade that broke exactly even (net == 0) counts as a
    loss, not a win - the same >0 test pro_study.stats() uses. None for a set with no trades."""
    if not rows:
        return None
    wins = [r["net"] for r in rows if r["net"] > 0]
    losses = [r["net"] for r in rows if r["net"] <= 0]
    return {"n": len(rows), "total": sum(r["net"] for r in rows),
            "win_rate": 100 * len(wins) / len(rows),
            "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
            "avg_loss": (sum(losses) / len(losses)) if losses else 0.0}


def fmt_summary(s):
    if s is None:
        return "no trades"
    return (f"{s['n']:>4} trades, ₹{s['total']:>+10,.0f} total, "
            f"{s['win_rate']:4.1f}% win rate, avg win ₹{s['avg_win']:>+8,.0f}, "
            f"avg loss ₹{s['avg_loss']:>+8,.0f}")


def main():
    print("Loading history (shared cache)...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
    stalled_arrs = {k: sts.stalled_series(hists[k], k, threshold=THRESHOLD) for k in INDICES}
    base_gate = {k: (lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k])) for k in INDICES}

    kept_rows, vetoed_rows = [], []
    for k in INDICES:
        df = hists[k]
        result = bt.run(k, df, gate=base_gate[k])
        pos = {t: n for n, t in enumerate(df.index)}
        sig = F[k]["sigma"].to_numpy()
        for tr in result["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            legs = ps.simulate(A[k], i, tr)
            p = ps.price(k, df, A[k], i, tr, legs, s)
            if not p:
                continue
            (vetoed_rows if stalled_arrs[k][i] else kept_rows).append(p)

    print("=" * 100)
    print(f" Trades today's real gate takes that a {THRESHOLD:.1f}-ATR stalled veto would refuse")
    print(" (the exact trades removed between the baseline and filtered rows of the earlier study)")
    print("=" * 100)
    for label, lo, hi in (("IN-SAMPLE  ", pd.Timestamp.min.tz_localize("UTC"), SPLIT),
                          ("OUT-OF-SAMPLE", SPLIT, pd.Timestamp.max.tz_localize("UTC"))):
        v = period(vetoed_rows, lo, hi)
        k_ = period(kept_rows, lo, hi)
        print(f"\n {label}")
        print(f"   Vetoed (removed by the filter):  {fmt_summary(summarise(v))}")
        print(f"   Kept (still taken by the filter): {fmt_summary(summarise(k_))}")

    print("\n" + "-" * 100)
    is_vetoed_total = sum(r["net"] for r in period(vetoed_rows, pd.Timestamp.min.tz_localize("UTC"), SPLIT))
    oos_vetoed_total = sum(r["net"] for r in period(vetoed_rows, SPLIT, pd.Timestamp.max.tz_localize("UTC")))
    print(f" The vetoed trades' own total: in-sample ₹{is_vetoed_total:>+10,.0f}"
          f"   out-of-sample ₹{oos_vetoed_total:>+10,.0f}")
    print(" A NEGATIVE vetoed-total in-sample and a POSITIVE one out-of-sample would mean the")
    print(" filter was simply removing bad trades in-sample and good ones out-of-sample - i.e. the")
    print(" SAME rule (not a different regime) behaves oppositely in the two periods.")
    return {"kept": kept_rows, "vetoed": vetoed_rows}


if __name__ == "__main__":
    main()
