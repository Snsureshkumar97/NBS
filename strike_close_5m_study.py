#!/usr/bin/env python3
"""
strike_close_5m_study.py — enter only after a 5-minute candle closes beyond the strike the tool gave
================================================================================
The user, 22 Oct 2026, after strike_close_study.py (15-minute closes) dropped: "for example today nifty 22600pe if the
tool had enter the trade after the index price closes below 22600 5 minutes candle after that we enter". That morning
the tool bought the 22600 PE at 09:30 (Nifty 22,605, premium 161.70); the 09:30-09:35 candle closed at 22,561 - the
rule would have bought at ~185 at 09:35, near the put's high, before Nifty turned up to 22,785.

ON 3 YEARS OF 5-MINUTE INDEX CANDLES, through entry_timing_study.py's harness - every 15-minute candle read after 5,
after 10 and at 15 minutes through the engine's own code and the live entry checks, the same exits for every variant
(live, on 5-minute candles), one position at a time per index, 20 minutes before the same way again, options priced
per lot after costs. Nifty, Bank Nifty and Sensex (there is no 5-minute history for Midcap).

FIXED BEFORE ANY RESULT WAS SEEN (22 Oct 2026):
  first signal         enter at the first read that says so - what live does (to the nearest 5 minutes)
  strike close (5 min) the strike is the one the tool gives at the run's first read (nearest the index then); enter
                       at the first 5-minute close, while every read since still says the same way, that is beyond
                       it - below for a put, above for a call (at once if that first read already is); if the signal
                       ends first, no trade
Kept only if better than "first signal" in BOTH periods (in-sample to 15 Aug 2025, then held-out), pooled and not
reversed on any index. Also how many runs were already beyond, waited, never got there, and what the waiting cost in
entry price.

RESULT (22 Oct 2026; strike_close_5m_study_results.txt) - DROP, NOTHING BUILT. Against "first signal", per lot after
costs, in-sample / held-out: pooled -83,694 / -41,614 (walk-forward 1 of 7 half-years), worst drop deeper by 17,359 /
10,625; Nifty +7,212 / -7,298 (more per trade, fewer trades - a wash), Bank Nifty -25,721 / -3,678, Sensex -65,184 /
-30,639. Half the runs are already beyond the strike at the first read; about a fifth wait ~13-15 minutes and buy
later; 27-30% never close through it and are skipped.

    /usr/bin/python3 strike_close_5m_study.py
"""
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import entry_timing_study as ets

VARIANTS = ("first signal", "strike close (5 min)")


def strike_candidates(key, sigs):
    """The reads the strike rule would enter on, and what happened to each run of signal reads."""
    step = config.INSTRUMENTS[key]["strike_step"]
    out, stats = [], {"runs": 0, "at_once": 0, "waited": 0, "wait_min": 0, "never": 0}
    run_side, run_k, last_t, run_start, done = None, None, None, None, True
    for now, i, k, r in sigs:
        if r is None:
            if run_side is not None and not done:
                stats["never"] += 1
            run_side, done = None, True
            continue
        side, spot = r[0], r[1]
        fresh = run_side != side or last_t is None or (now - last_t) > pd.Timedelta(minutes=5)
        if fresh:
            if run_side is not None and not done:
                stats["never"] += 1
            stats["runs"] += 1
            run_side, run_k, run_start, done = side, round(spot / step) * step, now, False
        last_t = now
        beyond = spot > run_k if side == "CE" else spot < run_k
        if beyond:
            out.append((now, i, k, r))
            if not done:
                done = True
                if now == run_start:
                    stats["at_once"] += 1
                else:
                    stats["waited"] += 1
                    stats["wait_min"] += (now - run_start).total_seconds() / 60
    if run_side is not None and not done:
        stats["never"] += 1
    return out, stats


def run_on(key, cands, df5, df15, st15, sigma, days, target):
    """entry_timing_study.run_variant, on a given list of candidate reads."""
    real = ets.select
    ets.select = lambda sigs, variant: cands
    try:
        return ets.run_variant(key, None, "x", df5, df15, st15, sigma, days, target)
    finally:
        ets.select = real


def main():
    import backtest_intraday as bt
    import indicators as ind
    import pro_study as ps
    import regime_study as rs
    import stop_day_study as sds
    print("Reading every 15-minute candle three times as it forms (this takes a while)...", flush=True)
    with ProcessPoolExecutor(max_workers=3) as ex:
        sigs = dict(ex.map(ets.signals_for, [(k,) for k in ets.INDICES]))
    vix = rs.load_vix()
    hist = {k: bt.fetch_history(k, years=3, use_cache=True) for k in ets.INDICES}
    nrv = np.log(rs.daily_close(hist["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    target = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    results, stats = {v: [] for v in VARIANTS}, {}
    for key in ets.INDICES:
        df15 = hist[key]
        F = rs.features(df15, vix, nrv)
        st15 = ind.supertrend(df15, *config.supertrend_params(key))[0].to_numpy()
        df5, sig, days = ets.load_5m(key), F["sigma"].to_numpy(), set(df15.index.date)
        first = ets.select(sigs[key], "first signal")
        cands, stats[key] = strike_candidates(key, sigs[key])
        for v, c in (("first signal", first), ("strike close (5 min)", cands)):
            results[v] += [dict(r, when=r["when"]) for r in run_on(key, c, df5, df15, st15, sig, days, target)]

    def split(rows):
        empty = {"n": 0, "total": 0, "pf": 0, "dd": 0, "win": 0, "avg": 0}
        return {"is": ps.stats([r for r in rows if r["when"] < rs.SPLIT]) or empty,
                "oos": ps.stats([r for r in rows if r["when"] >= rs.SPLIT]) or empty}
    W = 150
    print("=" * W)
    print(" ENTER ONLY AFTER A 5-MINUTE CLOSE BEYOND THE STRIKE - Nifty + Bank Nifty + Sensex, per lot after costs, the same"
          " exits for both (live, on 5-minute candles)")
    print("=" * W)
    print(f" {'entry':22s} {'IN-SAMPLE: trades, won, total, per trade, profit factor, worst drop':72s} | HELD-OUT FINAL YEAR")

    def show(name, rows):
        r = split(rows)
        a, b = r["is"], r["oos"]
        f = lambda s: (f"{s['n']:>5} {s['win']:>3.0f}% ₹{s['total']:>+10,.0f} ₹{(s['total'] / s['n'] if s['n'] else 0):>+5,.0f}/tr "
                       f"PF {s['pf']:.2f} DD ₹{s['dd']:>8,.0f}")
        print(f" {name:22s} {f(a):72s} | {f(b)}")
        return r
    base = {}
    for v in VARIANTS:
        base[v] = show(v, results[v])
    for key in ets.INDICES:
        print(f" {key}")
        for v in VARIANTS:
            show("   " + v, [r for r in results[v] if r["index"] == key])
    print("\n THE SIGNAL RUNS UNDER THE STRIKE RULE (every run of reads, before one-position and the cooldown)")
    for key in ets.INDICES:
        s = stats[key]
        n = s["runs"] or 1
        print(f"   {key:10s} {s['runs']:>5} runs: beyond the strike at once {s['at_once'] / n:5.1%}, waited and got there"
              f" {s['waited'] / n:5.1%} (on average {s['wait_min'] / max(1, s['waited']):.0f} minutes), never {s['never'] / n:5.1%}")
    d_is = base[VARIANTS[1]]["is"]["total"] - base[VARIANTS[0]]["is"]["total"]
    d_oos = base[VARIANTS[1]]["oos"]["total"] - base[VARIANTS[0]]["oos"]["total"]
    per = []
    for key in ets.INDICES:
        a = split([r for r in results[VARIANTS[1]] if r["index"] == key])
        b = split([r for r in results[VARIANTS[0]] if r["index"] == key])
        per.append((key, a["is"]["total"] - b["is"]["total"], a["oos"]["total"] - b["oos"]["total"]))
    keep = d_is > 0 and d_oos > 0 and all(x > 0 or y > 0 for _, x, y in per)
    print(f"\n VERDICT - {'KEEP' if keep else 'DROP'}: strike close (5 min) against first signal, in-sample {d_is:+,.0f}, held-out"
          f" {d_oos:+,.0f}; " + "; ".join(f"{k} {x:+,.0f} / {y:+,.0f}" for k, x, y in per))
    print("   " + sds.verdict("strike close (5 min)", results[VARIANTS[1]], results[VARIANTS[0]]).strip()
          .replace("\n", "\n   "))
    return results


if __name__ == "__main__":
    main()
