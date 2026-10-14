#!/usr/bin/env python3
"""
breadth_study.py — market breadth as a vote on the rules (3 years, Nifty / Sensex / Bank Nifty)
================================================================================
The user, 14 Oct 2026: "is there anything i need to add in the vote list that can improve the tool accuracy", then
"yes run the breadth test". Every vote tried before (volume, +DI/-DI, Bollinger, ADX rising, RSI cap, candle patterns,
1-hour trend, TradingView's 26) is read from the index's own candles and agrees with the EMA Trend vote 79-92% of the
time. Breadth - how many of the index's own stocks are moving the index's way - is the one thing the tool shows (the
market map, market_map.breadth) that is NOT the index's own price, and it had never been tested.

DATA: 15-minute candles of every stock in market_map.CONSTITUENTS (56 stocks: Nifty 50, Bank Nifty's 12, Sensex's 30),
1 Sep 2023 to 14 Oct 2026, from the admin's Kite login (~/trading-tool-logs/history/stocks/<SYM>_15m.csv). TODAY's
member lists are used for all three years, so stocks that joined later (and left-out stocks that fell out) flatter
breadth slightly - a known bias, the same for every variant.

BREADTH at a signal candle's close (only what was known then): for each stock, its close in the same 15-minute candle
against its previous day's close (the market map's "day change") - up, down, or flat; the share of the index's stocks
up / down, equal weight, or by the approximate index weights in market_map (WT). OPEN uses each stock's close against
its own open today instead.

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026). The base is the rules as live runs them from today (four_ideas_study's
BASE: the room from the day's range on the ~10-day normal day, out at T3 with the stop to T1 / T2, the Supertrend
trail after T1, the 2-hour breakeven, the T1-exit re-entry, at the money on the real expiry, 0.25% slippage, every
charge, per lot). Each variant adds ONE entry condition (a blocked signal frees the slot as it would live):
  EQ50     a CE only when at least half the stocks are up on the day; a PE only when at least half are down
  EQ60     the same at 60%
  WT50     EQ50 by index weight (the heavyweights count more - closer to what moves the index)
  OPEN50   EQ50 with each stock against its own open today (today's move only, not the gap)
  VETO40   only the clear disagreements are blocked: a CE when under 40% of the stocks are up, a PE when under 40% down
VERDICT: kept only if more than BASE in BOTH the first two years and the held-out year, Nifty + Sensex pooled (the
indices that run the rules on both accounts). Bank Nifty is shown but runs the TradingView rating on both accounts.
Five variants are tried, so one passing by luck is possible: a pass also has to hold on each index on its own.

RESULT (14 Oct 2026, history to 11 Sep 2026; breadth_study_results.txt) - NONE BUILT. Nifty + Sensex, per lot, first
two years / held-out, against BASE +394,292 / +191,738:
  EQ50     -22,488 / -38,937   DROP
  EQ60     -34,822 / -44,081   DROP
  WT50     +28,548 /  +3,500   passes pooled, but FAILS on Nifty alone (held-out -5,920) and Bank Nifty loses -83,800 /
                               -21,766; the held-out gain is under 2% with five variants tried - read as luck. Not built.
  OPEN50   -38,616 / -42,942   DROP
  VETO40    -5,066 /  -1,224   DROP
Why: breadth sides with the index's own day move 88% of the time (84% on Bank Nifty), and 72% of Nifty's trades are
already taken with 60%+ of the stocks agreeing. The trades taken AGAINST breadth (under 40% agreeing) are not the bad
ones - Nifty's earned PF 1.37 / 3.00, Bank Nifty's 3.06 in the first two years - so blocking them throws away winners.

    /usr/bin/python3 breadth_study.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
INDICES = ("NIFTY", "SENSEX", "BANKNIFTY")
VERDICT_ON = ("NIFTY", "SENSEX")
VARIANTS = ("BASE", "EQ50", "EQ60", "WT50", "OPEN50", "VETO40")
SENSEX_BASE = (225647, 119188)                     # four_ideas_study BASE, 15-minute
STOCKS = os.path.expanduser("~/trading-tool-logs/history/stocks")


def breadth(df, key):
    """For each candle of the index's df: (share up, share down) against the previous day's close, equal weight and
    by weight, and (share up, share down) against each stock's own open today. Only that candle's closes are used."""
    import numpy as np
    import pandas as pd
    import market_map
    members = market_map.CONSTITUENTS[key]
    close, prev, opn, w = {}, {}, {}, {}
    for sym, _, wt in members:
        s = pd.read_csv(os.path.join(STOCKS, f"{sym}_15m.csv"), index_col=0, parse_dates=True)
        day = pd.Series(s.index.date, index=s.index)
        last = s["Close"].groupby(day.values).last()
        first = s["Open"].groupby(day.values).first()
        prev_c = last.shift(1)
        c = s["Close"].reindex(df.index)
        d = pd.Series(df.index.date, index=df.index)
        close[sym] = c
        prev[sym] = d.map(prev_c)
        opn[sym] = d.map(first)
        w[sym] = wt
    C, P, O = pd.DataFrame(close), pd.DataFrame(prev), pd.DataFrame(opn)
    W = pd.Series(w)
    ok = C.notna() & P.notna()
    up, dn = (C > P) & ok, (C < P) & ok
    n = ok.sum(axis=1).replace(0, np.nan)
    wn = (ok * W).sum(axis=1).replace(0, np.nan)
    ok_o = C.notna() & O.notna()
    n_o = ok_o.sum(axis=1).replace(0, np.nan)
    return {"up": (up.sum(axis=1) / n).to_numpy(), "dn": (dn.sum(axis=1) / n).to_numpy(),
            "wup": ((up * W).sum(axis=1) / wn).to_numpy(), "wdn": ((dn * W).sum(axis=1) / wn).to_numpy(),
            "oup": (((C > O) & ok_o).sum(axis=1) / n_o).to_numpy(), "odn": (((C < O) & ok_o).sum(axis=1) / n_o).to_numpy(),
            "cover": ok.sum(axis=1).to_numpy(), "members": len(members)}


def allowed(variant, B, i, ce):
    """Whether the variant lets a CE (ce=True) or PE through at candle i. A candle with no stock data blocks nothing."""
    up, dn = (B["up"][i], B["dn"][i])
    if not (up == up):
        return True
    if variant == "EQ50":
        return (up if ce else dn) >= 0.5
    if variant == "EQ60":
        return (up if ce else dn) >= 0.6
    if variant == "WT50":
        return (B["wup"][i] if ce else B["wdn"][i]) >= 0.5
    if variant == "OPEN50":
        x = B["oup"][i] if ce else B["odn"][i]
        return not (x == x) or x >= 0.5
    if variant == "VETO40":
        return (up if ce else dn) >= 0.4
    return True


def _job(job):
    key, variant = job
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import indicators as ind
    import live_room_study as lrs
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    import t1_reentry_study as t1r
    import t3_exit_recheck_study as t3
    ps.SLIP = 0.0025
    lrs.install_live_room(key, chain=False, normal_days=10)        # today's live room (room_fix_study's C)
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    F = rs.features(df, vix, nrv)
    B = breadth(df, key)
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
         "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(), "days": set(df.index.date)}
    pre = bt.precompute(df, key)
    st = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    pos = {t: n for n, t in enumerate(df.index)}
    days = A["days"]
    expiry_day = lambda d: ps.expiry_on_or_after(key, d, days) == d
    gate = lambda i, rec: res.live_gate(key, i, rec, df) and allowed(variant, B, i, rec["option_type"] == "CE")
    trades = bt.run(key, df, gate=gate)["trades"]
    sig = F["sigma"].to_numpy()
    adx = pre["adx"]
    rows = []
    for tr in trades:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        legs, how, t1_done = t3.sim(A, i, tr, st, "t3", step_t2=True)
        p = ps.price(key, df, A, i, tr, legs, s)
        if p is None:
            continue
        px, j, _ = legs[-1]
        ce = tr["side"] == "CE"
        p["side"] = tr["side"]
        p["closed_via"] = ("stop" if abs(px - tr["stop"]) < 1e-6 else
                           "target" if tr.get("t3") is not None and abs(px - tr["t3"]) < 1e-6 else "other")
        p["exit_adx"] = float(adx.iloc[j])
        p["index"] = key
        p["t1_exit"] = how == "stop" and t1_done
        p["expiry_day"] = expiry_day(df.index[i].date())
        p["agree"] = B["up"][i] if ce else B["dn"][i]          # the share of stocks moving the trade's way
        p["cover"] = int(B["cover"][i])
        rows.append(p)
    rows.sort(key=lambda r: r["when"])
    return job, t1r.sequential(rows, config.REENTRY_COOLDOWN_MIN, config.ADX_TREND_THRESHOLD, "A")


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, v) for k in INDICES for v in VARIANTS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = sds.split(got[("SENSEX", "BASE")])
    assert (round(s["is"]["total"]), round(s["oos"]["total"])) == SENSEX_BASE, \
        f"SENSEX BASE must be four_ideas_study's BASE, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"
    pool_ = lambda v, ks=VERDICT_ON: sorted([r for k in ks for r in got[(k, v)]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" MARKET BREADTH AS A VOTE, on the rules as live runs them from 14 Oct 2026 (15-minute). Per lot, after costs."
          "  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" NIFTY + SENSEX (the verdict)")
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(v)))
    for k in INDICES:
        print(f" {k}")
        for v in VARIANTS:
            print(sds.line("   " + v, got[(k, v)]))
    print("\n HOW OFTEN BREADTH ALREADY AGREES with BASE's trades (share of the index's stocks moving the trade's way),"
          " and what each group earned")
    for k in INDICES:
        base = got[(k, "BASE")]
        cov = [r["cover"] for r in base]
        print(f" {k}: {len(base)} trades, stock data on {sum(1 for c in cov if c > 0)} of them")
        for name, lo, hi in (("under 40% agree", 0.0, 0.4), ("40-50%", 0.4, 0.5), ("50-60%", 0.5, 0.6), ("60%+ agree", 0.6, 1.01)):
            g = [r for r in base if r["agree"] == r["agree"] and lo <= r["agree"] < hi]
            print(sds.line(f"   {name}", g))
    print("\n VERDICT - kept only if more than BASE in BOTH periods (Nifty + Sensex pooled), and then on each index too")
    for v in VARIANTS[1:]:
        print("  " + sds.verdict(v, pool_(v), pool_("BASE")).strip())
        for k in INDICES:
            print("      " + sds.verdict(f"{v} {k}", got[(k, v)], got[(k, "BASE")]).strip())
    return got


if __name__ == "__main__":
    main()
