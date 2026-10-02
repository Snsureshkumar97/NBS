#!/usr/bin/env python3
"""
volume_vote_study.py — add volume, or swap it in for MACD - 3yr NIFTY/Bank Nifty
================================================================================
The user, 2 Oct 2026: "add volume and backtest... remove macd and add volume
and backtest... check which one gives better results." Tests
config.VOLUME_VOTE_MODE's two real modes against today's live rules ("off").

SENSEX IS NOT HERE - by the user's own choice, 2 Oct 2026, after real
searching turned up no comparably available BSE futures archive (NSE's own
public bhavcopy covers NIFTY/Bank Nifty cleanly - nse_futures_volume.py).
SENSEX's own backtest is unaffected either way: VOLUME_VOTE_MODE is global,
but nse_futures_volume.py never has a SENSEX reading, so its volume dict is
always {"available": False} there and build_recommendation() falls straight
back through to today's un-volume-touched behaviour for it specifically.

THE VOTE ITSELF - a judgement call, flagged as one
    "Rising participation" has no sign of its own - it only means something
    read against which way price actually moved, the standard OBV-style
    reading. Built from this project's own prior building blocks, not
    invented fresh: the volume oscillator formula (EMA5 vs EMA20 of volume,
    in percent) is gann_volume_study.py's, unchanged. Score, per day,
    shifted by one full day before it ever reaches a bar (a bar during day D
    can only know what NSE's bhavcopy for day D-1 PUBLISHED, never day D's
    own not-yet-finished volume - the same discipline regime_study.py's VIX
    shift already uses):
        oscillator > 0 (participation expanding) AND the future closed up
            that day  -> +1 (bullish)
        oscillator > 0 AND closed down                -> -1 (bearish)
        oscillator <= 0 (fading)                        -> 0 (abstains -
            fading volume does not confirm a move either way)
    This is a judgement call about what "add volume" should mean as a
    VOTE (the other four are all directional), not a transcript of anything
    the user specified in that much detail - worth checking against intent
    before trusting the result.

    python3 volume_vote_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import nse_futures_volume as nfv
import pro_study as ps
import regime_study as rs

INDICES = ("NIFTY", "BANKNIFTY")      # SENSEX excluded - see docstring
SPLIT = rs.SPLIT
VO_FAST, VO_SLOW = 5, 20               # gann_volume_study.py's own oscillator, unchanged


def volume_oscillator(volume):
    v = pd.Series(volume, dtype=float)
    ef, es = v.ewm(span=VO_FAST, adjust=False).mean(), v.ewm(span=VO_SLOW, adjust=False).mean()
    return (ef - es) / es.replace(0, np.nan) * 100.0


def volume_score_by_date(symbol):
    """{date: -1/0/+1}, already shifted one day - see this file's own
    docstring for the exact rule and why the shift is mandatory, not
    optional."""
    raw = nfv.fetch([], use_cache=True)     # trading_days=[] - cache-only, nothing new fetched
    fm = nfv.front_month_series(raw, symbol)
    if fm.empty:
        return {}
    osc = volume_oscillator(fm["volume"])
    direction = np.sign(fm["close"] - fm["open"])
    score = pd.Series(np.where(osc > 0, direction, 0.0), index=fm.index)
    score = score.shift(1)          # the look-ahead-safe shift, mandatory
    return {d.date(): int(v) for d, v in score.items() if pd.notna(v)}


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}

    print("Loading NSE futures volume (cache-only - run nse_futures_volume.py first)...")
    vol_by_date = {k: volume_score_by_date(k) for k in INDICES}
    for k in INDICES:
        n = len(vol_by_date[k])
        qualifying = sum(1 for v in vol_by_date[k].values() if v != 0)
        print(f"  {k}: {n} days with a (shifted) volume reading, {qualifying} actually voting "
              f"(the rest had fading participation and abstain)")

    was_mode = config.VOLUME_VOTE_MODE

    def evaluate(mode):
        config.VOLUME_VOTE_MODE = mode
        rows = []
        for k in INDICES:
            df = hists[k]
            res = bt.run(k, df, gate=base_gate[k], volume_by_date=vol_by_date[k] or None)
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in res["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = ps.simulate(A[k], i, tr)
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:34s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    try:
        print("=" * 128)
        print(" NIFTY + Bank Nifty only (SENSEX excluded - see docstring), real expiries, after costs")
        print("=" * 128)
        hdr = (f" {'variant':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
               f"  | OUT-OF-SAMPLE (held-out final year)")
        print(hdr); print("-" * 128)

        base = evaluate("off")
        print(line("LIVE RULES (volume off, today)", base))
        add = evaluate("add")
        print(line("volume ADDED as a 5th vote", add))
        swap = evaluate("replace_macd")
        print(line("volume REPLACES MACD's vote", swap))

        print("\n VERDICT — kept only if better than LIVE RULES both in-sample and out-of-sample")
        for name, r in (("volume added", add), ("volume replaces MACD", swap)):
            di = r["is"]["total"] - base["is"]["total"]; do = r["oos"]["total"] - base["oos"]["total"]
            dd = r["oos"]["dd"] - base["oos"]["dd"]
            keep = di > 0 and do > 0
            print(f"   {'KEEP ' if keep else 'drop '} {name:28s} in-sample {di:>+10,.0f}"
                  f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
        return {"off": base, "add": add, "replace_macd": swap}
    finally:
        config.VOLUME_VOTE_MODE = was_mode


if __name__ == "__main__":
    main()
