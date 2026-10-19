#!/usr/bin/env python3
"""
order_block_fvg_check.py — a check, not a decision (20 Oct 2026, after order_block_study.py): does order_block_study's FVG result (Bank Nifty's TradingView system, Nifty's rules) hold
when the gap may have formed in the last 1, 2, 4 or 5 candles instead of 3, and on Nifty's TradingView system (offered,
not used)? A real effect should not live at one window only. Also profit by half-year against today's.
RESULT: see order_block_study.py's docstring (THE FVG CHECK) and order_block_fvg_check_results.txt.

    /usr/bin/python3 order_block_fvg_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
WINDOWS = (0, 1, 2, 3, 4, 5)          # 0 = today's system, no FVG condition
TV = {"NIFTY": (267670, 143011), "BANKNIFTY": (181285, 76234)}


def with_window(S, df, n):
    import numpy as np
    lo, hi = df["Low"].to_numpy(float), df["High"].to_numpy(float)
    fb, fs = np.zeros(len(lo), bool), np.zeros(len(lo), bool)
    fb[2:], fs[2:] = lo[2:] > hi[:-2], hi[2:] < lo[:-2]

    def win(x):
        out = x.copy()
        for s in range(1, n):
            out[s:] |= x[:-s]
        return out
    S["fvg_bull"], S["fvg_bear"] = win(fb), win(fs)
    return S


def _job(job):
    system, key, n = job
    import numpy as np
    import backtest_intraday as bt
    import order_block_study as ob
    if system == "tv":
        import tv_ratings_study as tvs
        if n:
            real = tvs.tv_ratings

            def filtered(df, real=real):
                rt = real(df).copy()
                S = with_window(ob.smc(df, np.asarray(bt.precompute(df, key)["atr"], float)), df, n)
                r = rt["all"].to_numpy(float).copy()
                for i in range(len(r)):
                    if (r[i] > tvs.STRONG and not ob.allowed("FVG", S, i, True)) or \
                       (r[i] < -tvs.STRONG and not ob.allowed("FVG", S, i, False)):
                        r[i] = 0.0
                rt["all"] = r
                return rt
            tvs.tv_ratings = filtered
        _, out = tvs._tv_job(key)
        return job, out["tv_ours"]

    def make(df, pre):
        if not n:
            return None, None, None
        S = with_window(ob.smc(df, np.asarray(pre["atr"], float)), df, n)
        return (lambda i, buy: ob.allowed("FVG", S, i, buy)), None, None
    return job, ob.rules_rows(key, make)


def main():
    import multiprocessing as mp
    import pandas as pd
    import stop_day_study as sds
    jobs = [("tv", k, n) for k in TV for n in WINDOWS] + [("rules", "NIFTY", n) for n in WINDOWS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for k, figs in TV.items():
        s = sds.split(got[("tv", k, 0)])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} TV base must be {figs}"
    s = sds.split(got[("rules", "NIFTY", 0)])
    assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (60462, 51814), "Nifty rules base (order_block_study)"
    print("FVG WINDOW CHECK (a check, not a decision) - per lot after costs, first two years | held-out")
    for system, key in (("tv", "BANKNIFTY"), ("tv", "NIFTY"), ("rules", "NIFTY")):
        print(f" {key} {'TradingView rating' if system == 'tv' else 'rules'}")
        for n in WINDOWS:
            print(sds.line(f"   {'today (no FVG)' if not n else f'FVG in the last {n} candle(s)'}", got[(system, key, n)]))
        for n in WINDOWS[1:]:
            print("     " + sds.verdict(f"FVG {n} vs today", got[(system, key, n)], got[(system, key, 0)]).strip())
        base, fvg = got[(system, key, 0)], got[(system, key, 3)]
        half = lambda r: f"{r['when'].year}-H{1 if r['when'].month <= 6 else 2}"
        b = pd.Series([r["net"] for r in base], index=[half(r) for r in base]).groupby(level=0).sum()
        f = pd.Series([r["net"] for r in fvg], index=[half(r) for r in fvg]).groupby(level=0).sum()
        print("     by half-year, FVG(3) minus today: " + "  ".join(f"{h} {f.get(h, 0) - b.get(h, 0):+,.0f}" for h in b.index))


if __name__ == "__main__":
    main()
