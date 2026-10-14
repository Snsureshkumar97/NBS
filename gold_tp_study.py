#!/usr/bin/env python3
"""
gold_tp_study.py — a real take-profit for gold's TradingView moving-average rule (Exness real ticks, 3 years)
================================================================================
The user, 14 Oct 2026: "i was also noticing the gold targets are way to far", then "yes do both" (a card that shows the
trail instead of the far targets, and this test).

Gold's live rule (config.CFD_RULES["GOLD"], cfd_rules._apply_tv): TradingView's MA rating Strong Buy / Sell at a
15-minute close; stop 3 x ATR; once 5 x ATR in profit a stop 2 x ATR behind the best price; an opposite Strong reading
reverses; 24 hours at most; a safety target 30 x ATR (never reached in 3 years - the trail is the real exit).

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026): the same rule with a REAL take-profit at 3, 5, 8 or 10 x ATR (the trail
unchanged), against TODAY (30 x ATR, asserted to reproduce crypto_tv_ratings_study's "MAs only, as built"). Walked
tick by tick (crypto_tv_ratings_study.walk_trail), Exness's spread in and out, its swap on buys, one position at a
time, no wait after an exit. $ per gold lot (100 oz). A take-profit is kept only if it makes more than TODAY in BOTH
the first two years (to 15 Aug 2025) and the held-out year.

RESULT (14 Oct 2026, ticks to Aug 2026; gold_tp_study_results.txt), $ per gold lot, first two years / held-out, against
TODAY (30 x ATR, safety only) +48,403 / +145,212:
  3 x ATR  -46,228 / -89,592   DROP      5 x ATR  -17,825 / -37,149   DROP      8 x ATR  -3,903 / +31,947   drop (mixed)
  10 x ATR +1,096 / +11,422    passes the rule - by +2% in the first two years: as good as today, not clearly better
Near targets cut gold's winners; most trades end on an opposite Strong reading (~2,560 of ~3,700) or the trail. The
target barely matters at 8 x ATR and beyond. Not changed: the user's call.

    python3 gold_tp_study.py
"""
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SYM = "XAUUSD"
TPS = (3.0, 5.0, 8.0, 10.0, 30.0)
TODAY_FIGS = (48403, 145212)          # crypto_tv_ratings_study_results.txt, gold "MAs only, as built (target 30 x ATR)"


def _job(args):
    y, m, path = args
    import backtest_intraday as bt
    import cfd_tick_study as t
    import crypto_tv_ratings_study as cts
    import exness_data as ed
    key, mult = cts.SYMBOLS[SYM]
    full = ed.load(SYM)
    df = full[["Open", "High", "Low", "Close", "Volume"]]
    with open(path, "rb") as fh:
        d = pickle.load(fh)
    t_lo = pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC")
    t_hi = t_lo + pd.offsets.MonthBegin(1)
    idx_utc = df.index.tz_convert("UTC")
    lo_i, hi_i = int(idx_utc.searchsorted(t_lo)), int(idx_utc.searchsorted(t_hi))
    parts = [p for p in (t._month_ticks(SYM, y, m), t._month_ticks(SYM, *((y + 1, 1) if m == 12 else (y, m + 1)))) if p is not None]
    if not parts:
        return []
    T = np.concatenate([p[0] for p in parts]); M = np.concatenate([p[1] for p in parts]); S = np.concatenate([p[2] for p in parts])
    adx_arr = bt.precompute(df, key)["adx"].to_numpy()
    cl = df["Close"].to_numpy()
    sp_c = full["spread_close"].to_numpy()
    bar_start = idx_utc.asi8
    opp = d["opp"]["ma"]
    out = []
    for k in TPS:
        for c in d["cands"]["tv_ma_tp"]:
            i = c["i"]
            if not (lo_i <= i < hi_i):
                continue
            ce = c["side"] == "CE"
            tp = c["entry"] + (k * c["atr"] if ce else -k * c["atr"])
            px, j, via, xs = cts.walk_trail(T, M, S, bar_start, cl, i, c["side"], c["entry"], c["stop"],
                                            5 * c["atr"], 2 * c["atr"], opp, target=tp)
            xs = sp_c[j] if not (xs == xs) else xs
            net = ((1 if ce else -1) * (px - c["entry"]) - sp_c[i] / 2 - xs / 2) * mult
            out.append({"tp": k, "when": df.index[i] + pd.Timedelta(minutes=15),
                        "exit_time": df.index[j] + pd.Timedelta(minutes=15), "side": c["side"],
                        "closed_via": "target" if via == "target" else ("stop" if via == "stop" else "other"),
                        "how": via, "exit_adx": float(adx_arr[j]), "net": net})
    return out


def main():
    from concurrent.futures import ProcessPoolExecutor
    from collections import Counter
    import cfd_tick_study as t
    import crypto_strategy_study as cs
    import crypto_tv_ratings_study as cts
    import exness_data as ed
    _, path = cts._prep(SYM)
    months = ed.months_until(t.LAST)
    rows = []
    with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
        for part in ex.map(_job, [(y, m, path) for (y, m) in months]):
            rows.extend(part)
    df = ed.load(SYM)[["Open", "High", "Low", "Close", "Volume"]]
    cts.add_swap_and_direction(rows, SYM, df)
    res = {k: cs.sequential([r for r in rows if r["tp"] == k]) for k in TPS}
    p30 = cs.periods(res[30.0])
    got = (round(cs.stats(p30["is"], "net")["total"]), round(cs.stats(p30["oos"], "net")["total"]))
    assert got == TODAY_FIGS, f"TODAY (30 x ATR) must reproduce {TODAY_FIGS}, got {got}"
    print("=" * 150)
    print(" GOLD: TradingView MA rule with a REAL take-profit (the trail unchanged), Exness real ticks, $ per gold lot after spread and swap")
    print("=" * 150)
    print(f" {'take-profit':28s} {'FIRST TWO YEARS  n  won   total    PF   worst drop':>52s} | {'HELD-OUT  n  won   total    PF   worst drop':>48s} | how they closed")

    def c(rs):
        s = cs.stats(rs, "net")
        return f"{s['n']:>5} {s['win']:>4.0f}% ${s['total']:>+10,.0f} {s['pf']:>5.2f} ${s['dd']:>9,.0f}"
    for k in TPS:
        p = cs.periods(res[k])
        how = Counter(r["how"] for r in res[k])
        lab = f"{k:g} x ATR" + (" (TODAY - safety only)" if k == 30.0 else "")
        print(f"   {lab:26s} {c(p['is'])} | {c(p['oos'])} | " + ", ".join(f"{w} {n}" for w, n in how.most_common()))
    base = cs.periods(res[30.0])
    print("\n VERDICT - kept only if more than TODAY in BOTH periods")
    for k in TPS[:-1]:
        p = cs.periods(res[k])
        di = cs.stats(p["is"], "net")["total"] - cs.stats(base["is"], "net")["total"]
        do = cs.stats(p["oos"], "net")["total"] - cs.stats(base["oos"], "net")["total"]
        print(f"   {'KEEP' if di > 0 and do > 0 else 'drop'}  take-profit {k:g} x ATR   {di:>+10,.0f} / {do:>+10,.0f}")
    return res


if __name__ == "__main__":
    main()
