#!/usr/bin/env python3
"""
four_ideas_study.py — four ideas from a pasted AI note, on the rules as they run from 14 Oct 2026 (3 years)
================================================================================
The user, 14 Oct 2026, after pasting an AI note on room to run, targets and option "upgrades": "build the room fix and
test all four ideas" - the four of its ideas the tool had never measured:
  1. no option buying when implied volatility is high ("do not buy options if the IV Percentile is above 80")
  2. the stop "just below the EMA 20 or the VWAP line" instead of the swing
  3. the target at "the next pivot point, or the 2nd standard-deviation VWAP band"
  4. an expiry-day profile ("take partial profits at 1:1 or 1:1.5", "reduce position sizes by 50% on expiry-day trades")

FIXED BEFORE ANY RESULT WAS SEEN (14 Oct 2026). The base is the rules as live runs them from today: the room to run
from the day's range alone on the ~10-day normal day (config.ROOM_LIMITS - room_fix_study's C, asserted), out at T3
with the stop to T1 at T1 and to T2 at T2, the Supertrend trail after T1, the 2-hour breakeven, the T1-exit re-entry
(t3_exit_recheck_study's walk, 15-minute candles), priced at the money on the real expiry, 0.25% slippage, every charge.
Each variant changes ONE thing:
  VIX80        skip an entry when India VIX's previous close is above the 80th percentile of its prior 252 days (the
               index options' own IV history is not kept; India VIX is the market's IV)
  STOP_EMA20   the stop at EMA 20 less 0.15 ATR (the engine's own swing rules: at least 0.5 ATR away or 0.75 ATR,
               at most 2 ATR) - EMA 20 handed to the engine in place of the 12-candle swing
  STOP_VWAP    the same with the session VWAP
  TGT_PIVOT    T3 at the nearest of the day's floor pivots (P, R1-R3 / S1-S3 off the previous day) beyond the entry;
               T1 / T2 at 40 / 70% of it; beyond R3 / S3 the room as today
  TGT_VWAP2SD  T3 at the session VWAP +/- 2 standard deviations; the room as today when the band is not beyond entry
  EXP_SKIP     no entry on an expiry day
  EXP_1_5R     on an expiry day, out at 1.5 x the risk (T1 / T2 at 40 / 70% of it); other days as today
  EXP_HALF     an expiry-day trade at half size (its P&L halved)
VERDICT: kept only if more than BASE in BOTH the first two years and the held-out year, Sensex + Midcap pooled (the two
indices that run the rules). Eight variants are tried, so one passing by luck is quite possible.

RESULT (14 Oct 2026, history to 11 Sep 2026; four_ideas_study_results.txt) - NONE BUILT. Sensex + Midcap, per lot,
first two years / held-out, against BASE +489,948 / +204,688:
  VIX80        -160,941 / -112,472  DROP - the high-volatility days are good days for a trend-follower
  STOP_EMA20   +23,402 / +2,366    passes the rule narrowly, but worse on Sensex in the first two years (-39,949) and on
               Midcap in the held-out year (-5,127), with a deeper worst drop; on Sensex's 5-minute walk (the most faithful)
               -30,398 / +5,911 (+169,336 / +69,002 against +199,733 / +63,091). Eight variants tried: read as luck. Not built.
  STOP_VWAP    +28,674 / -39,817   DROP
  TGT_PIVOT    -283,980 / -113,627 DROP - the nearest pivot is a much closer target (cuts the runners)
  TGT_VWAP2SD  -10,570 / -100,997  DROP
  EXP_SKIP     -154,994 / -64,412  DROP - expiry days are the BEST days: 305 trades, PF 2.02 / 1.68, +153,273 / +67,214
  EXP_1_5R     -45,431 / +26,662   DROP (mixed)
  EXP_HALF     -76,636 / -33,607   DROP

    python3 four_ideas_study.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("SENSEX", "MIDCPNIFTY")
VARIANTS = ("BASE", "VIX80", "STOP_EMA20", "STOP_VWAP", "TGT_PIVOT", "TGT_VWAP2SD", "EXP_SKIP", "EXP_1_5R", "EXP_HALF")
BASE_FIGS = {"SENSEX": (225647, 119188), "MIDCPNIFTY": (264301, 85500)}     # room_fix_study C, 15-minute


def _job(job):
    key, variant = job
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import index_candidates_study as ics
    import indicators as ind
    import live_room_study as lrs
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    import signal_engine as se
    import t1_reentry_study as t1r
    import t3_exit_recheck_study as t3
    ps.SLIP = 0.0025
    if key == "MIDCPNIFTY":
        real_s = ps._scheduled
        ps._scheduled = lambda k, d, real_s=real_s: ics._monthly(d) if k == key else real_s(k, d)
    lrs.install_live_room(key, chain=False, normal_days=10)        # today's live room (room_fix_study's C)
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    F = rs.features(df, vix, nrv)
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
         "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(), "days": set(df.index.date)}
    pre = bt.precompute(df, key)
    st = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    pos = {t: n for n, t in enumerate(df.index)}
    days = A["days"]
    expiry_day = lambda d: ps.expiry_on_or_after(key, d, days) == d

    reject = lambda i: False
    if variant == "VIX80":
        vs = pd.Series(vix.values, index=pd.to_datetime(list(vix.index))).sort_index()
        pct = vs.rolling(252, min_periods=120).apply(lambda x: (x[:-1] < x[-1]).mean() * 100, raw=True)
        prev = pct.shift(1)                                         # the previous day's close, known at the open
        pmap = {t.date(): v for t, v in prev.items()}
        reject = lambda i: (pmap.get(df.index[i].date(), float("nan")) or 0) > 80
    elif variant == "EXP_SKIP":
        reject = lambda i: expiry_day(df.index[i].date())
    if variant in ("STOP_EMA20", "STOP_VWAP"):
        lvl = (pre["ema_fast"] if variant == "STOP_EMA20" else pre["vwap"]).to_numpy(dtype=float)
        real_tech = bt.tech_at

        def tech_at(df_, pre_, i):
            t = real_tech(df_, pre_, i)
            if lvl[i] == lvl[i]:
                t["last_swing_low"] = t["last_swing_high"] = float(lvl[i])
            return t
        bt.tech_at = tech_at
    if variant in ("TGT_PIVOT", "TGT_VWAP2SD"):
        if variant == "TGT_PIVOT":
            d = df.groupby(df.index.date).agg(h=("High", "max"), l=("Low", "min"), c=("Close", "last"))
            P = (d["h"] + d["l"] + d["c"]) / 3
            lv = pd.DataFrame({"P": P, "R1": 2 * P - d["l"], "S1": 2 * P - d["h"], "R2": P + d["h"] - d["l"],
                               "S2": P - d["h"] + d["l"], "R3": d["h"] + 2 * (P - d["l"]), "S3": d["l"] - 2 * (d["h"] - P)}).shift(1)
            piv = {day_: row for day_, row in lv.iterrows()}

            def target(i, spot, up):
                row = piv.get(df.index[i].date())
                if row is None or row.isna().any():
                    return None
                cand = [row[k] for k in (("P", "R1", "R2", "R3") if up else ("P", "S1", "S2", "S3"))]
                cand = [x for x in cand if (x > spot if up else x < spot)]
                return (min(cand) if up else max(cand)) if cand else None
        else:
            tp = (df["High"] + df["Low"] + df["Close"]) / 3
            prof = config.INTRADAY_VOLUME_PROFILE
            w = pd.Series([prof.get(f"{t.hour:02d}:{(t.minute // 15) * 15:02d}", 1.0) for t in df.index], index=df.index)
            g = df.index.date
            vw = (tp * w).groupby(g).cumsum() / w.groupby(g).cumsum()
            var = ((tp * tp * w).groupby(g).cumsum() / w.groupby(g).cumsum() - vw * vw).clip(lower=0)
            upb, dnb = (vw + 2 * np.sqrt(var)).to_numpy(), (vw - 2 * np.sqrt(var)).to_numpy()

            def target(i, spot, up):
                x = upb[i] if up else dnb[i]
                return x if (x == x and (x > spot if up else x < spot)) else None
        inner = se.compute_reachability

        def reach_to_level(spot, oi, df_, now, **kw):
            out = inner(spot, oi, df_, now, **kw)
            i = pos.get(pd.Timestamp(now))
            if i is None:
                return out
            out = dict(out)
            for up, key_ in ((True, "reach_up"), (False, "reach_down")):
                x = target(i, spot, up)
                if x is not None:
                    out[key_] = round(abs(x - spot), 2)
                    out["cap_up" if up else "cap_down"] = "the pivot / band target"
            out["available"] = out.get("reach_up") is not None or out.get("reach_down") is not None
            return out
        se.compute_reachability = reach_to_level

    trades = bt.run(key, df, gate=lambda i, rec: res.live_gate(key, i, rec, df) and not reject(i))["trades"]
    sig = F["sigma"].to_numpy()
    adx = pre["adx"]
    rows = []
    for tr in trades:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        exp_d = expiry_day(df.index[i].date())
        if variant == "EXP_1_5R" and exp_d:
            ce = tr["side"] == "CE"
            R = abs(tr["entry"] - tr["stop"])
            T = 1.5 * R
            tr = dict(tr, t1=tr["entry"] + (0.4 * T if ce else -0.4 * T), t2=tr["entry"] + (0.7 * T if ce else -0.7 * T),
                      t3=tr["entry"] + (T if ce else -T))
        legs, how, t1_done = t3.sim(A, i, tr, st, "t3", step_t2=True)
        p = ps.price(key, df, A, i, tr, legs, s)
        if p is None:
            continue
        if variant == "EXP_HALF" and exp_d:
            p["net"] *= 0.5
        px, j, _ = legs[-1]
        p["side"] = tr["side"]
        p["closed_via"] = ("stop" if abs(px - tr["stop"]) < 1e-6 else
                           "target" if tr.get("t3") is not None and abs(px - tr["t3"]) < 1e-6 else "other")
        p["exit_adx"] = float(adx.iloc[j])
        p["index"] = key
        p["t1_exit"] = how == "stop" and t1_done
        p["expiry_day"] = exp_d
        rows.append(p)
    rows.sort(key=lambda r: r["when"])
    return job, t1r.sequential(rows, config.REENTRY_COOLDOWN_MIN, config.ADX_TREND_THRESHOLD, "A")


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, v) for k in RULES for v in VARIANTS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for k, (a, b) in BASE_FIGS.items():
        s = sds.split(got[(k, "BASE")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), \
            f"{k}: BASE must be room_fix_study's C, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"
    pool_ = lambda v: sorted([r for k in RULES for r in got[(k, v)]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" FOUR IDEAS FROM THE PASTED NOTE, on the rules as live runs them from 14 Oct 2026 (Sensex + Midcap, 15-minute)."
          " Per lot, after costs.  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(v)))
    for k in RULES:
        print(f" {k}")
        for v in VARIANTS:
            print(sds.line("   " + v, got[(k, v)]))
    exp = [r for r in pool_("BASE") if r.get("expiry_day")]
    print(f"\n BASE's expiry-day trades: {len(exp)} of {len(pool_('BASE'))}")
    print(sds.line("   BASE, expiry days only", exp))
    print("\n VERDICT - kept only if more than BASE in BOTH periods (Sensex + Midcap)")
    for v in VARIANTS[1:]:
        print("  " + sds.verdict(v, pool_(v), pool_("BASE")).strip())
    return got


if __name__ == "__main__":
    main()
