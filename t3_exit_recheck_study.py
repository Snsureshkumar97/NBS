#!/usr/bin/env python3
"""
t3_exit_recheck_study.py — out at T3 instead of T2, now that a T1 exit can re-enter at once (Sensex + Midcap, 3 years)
================================================================================
The user, 13 Oct 2026: "run the T3 exit re-check with the re-entry rule".

On 7 Oct stop_target_sweep.py found out-at-T3 ahead of out-at-T2 on every index in both periods; the user kept T2.
Two things have changed since: Nifty and Bank Nifty run the Trend Rider (so only Sensex and Midcap Select exit at a
rules target), and today a T1 exit may re-enter straight away when ADX is still 20 or more
(config.WAIVE_COOLDOWN_AFTER_T1_EXIT, t1_reentry_study.py). And one thing that sweep walked differently from live:
with the exit at T3, tickets._check_price moves the stop to EVERY rung before the exit - T1 at T1, T2 at T2 - while
opening_window_study.simulate_live moves it only at T1.

FIXED BEFORE ANY RESULT WAS SEEN (13 Oct 2026), the tool's rules harness (bollinger_vote_study's baseline asserted):
    T2              out at T2, the cooldown waived only after a trending target         (until today; the baseline)
    T2 + re-entry   out at T2 and the T1-exit waiver                                    (LIVE NOW - the comparison)
    T3 + re-entry   out at T3: the stop to T1 at T1 and to T2 at T2, the Supertrend trail after T1, the 2-hour
                    breakeven - as _check_price does it - and the T1-exit waiver           (THE CANDIDATE)
  and for information: T3 without the T1-exit waiver; T3 walked as on 7 Oct (no step at T2) with it.
VERDICT: T3 + re-entry is better than what runs now only if it makes more in BOTH the first two years and the
held-out final year, Sensex + Midcap pooled. Per lot, after costs.

RESULT (13 Oct 2026, history to 11 Sep 2026; t3_exit_recheck_study_results.txt) - T3 + re-entry KEEP against what
runs now. Sensex + Midcap, first two years / held-out: T2 + re-entry (live) +303,873 / +197,048; T3 + re-entry
+370,798 / +229,174 (+66,926 / +32,126), worst drop smaller (-358 / -4,448). Both indices better in both periods:
Sensex +40,460 / +21,953, Midcap +26,466 / +10,174. Without the re-entry, T3 vs T2 +30,032 / +31,373 (7 Oct's finding
holds on these two indices). Walking T3 as on 7 Oct (no stop step at T2) understates it: +339,371 / +201,913. The
change would be config.EXIT_AT_TARGET = "T3" - it reaches only the rules' tickets (the Trend Rider, the Exness rules
and the AI desk fix their own exit at T3). Not changed: the user's call (they kept T2 on 7 Oct).

    python3 t3_exit_recheck_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("SENSEX", "MIDCPNIFTY")


def sim(A, i, tr, st, target, step_t2=False, wait_bars=None, hold_bars=26):
    """opening_window_study.simulate_live (realistic breakeven), out at `target`; step_t2 also moves the stop to T2
    when T2 is touched (a T3 exit, as tickets._check_price does). -> ([(exit spot, bar, 1.0)], how, T1 touched)."""
    import fills
    import opening_window_study as ows
    wait_bars = ows.WAIT_BARS if wait_bars is None else wait_bars
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    tighten = (lambda a, b: max(a, b)) if ce else (lambda a, b: min(a, b))
    stop, t1_done, t2_done, be_done = tr["stop"], False, False, False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(fills.stop_fill(A, j, stop, ce), j, 1.0)], "stop", t1_done    # fills.py: open when past
        if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_done = True
            stop = tighten(stop, tr["t1"])
        if step_t2 and not t2_done and ((hi[j] >= tr["t2"]) if ce else (lo[j] <= tr["t2"])):
            t2_done = True
            stop = tighten(stop, tr["t2"])
        if t1_done and st[j] == st[j]:
            stop = tighten(stop, st[j])
        tgt = tr.get(target)
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            return [(tgt, j, 1.0)], "target", t1_done
        if not t1_done and not be_done and (j - i) >= wait_bars:
            be_done = True
            if (cl[j] <= tr["entry"]) if ce else (cl[j] >= tr["entry"]):
                return [(cl[j], j, 1.0)], "breakeven-close", t1_done
            stop = tighten(stop, tr["entry"])
        if end[j]:
            return [(cl[j], j, 1.0)], "close", t1_done
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)], "close", t1_done


VARIANTS = (  # key, target, step at T2, waiver ("today" = trending target only, "A" = + the T1-exit waiver), label
    ("t2", "t2", False, "today", "T2 (until today)"),
    ("t2re", "t2", False, "A", "T2 + T1 re-entry (LIVE NOW)"),
    ("t3re", "t3", True, "A", "T3 + T1 re-entry (the candidate)"),
    ("t3", "t3", True, "today", "(info) T3, no T1 re-entry"),
    ("t3re_old", "t3", False, "A", "(info) T3 + re-entry, walked as on 7 Oct"),
)


def _job(key):
    import math
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import index_candidates_study as ics
    import indicators as ind
    import opening_window_study as ows
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    import t1_reentry_study as t1r
    ps.SLIP = 0.0025
    if key == "MIDCPNIFTY":
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    F = rs.features(df, vix, nrv)
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(), "op": df["Open"].to_numpy(),
         "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(), "days": set(df.index.date)}
    pre = bt.precompute(df, key)
    st = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    trades = bt.run(key, df, gate=lambda i, rec: res.live_gate(key, i, rec, df))["trades"]
    pos = {t: n for n, t in enumerate(df.index)}
    sig = F["sigma"].to_numpy()
    adx = pre["adx"]
    out = {}
    for vkey, target, step_t2, waiver, _ in VARIANTS:
        rows = []
        for tr in trades:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            legs, how, t1_done = sim(A, i, tr, st, target, step_t2)
            if not step_t2:
                assert legs == ows.simulate_live(A, i, tr, st, target, realistic_be=True), "must be simulate_live"
            p = ps.price(key, df, A, i, tr, legs, s)
            if p is None:
                continue
            px, j, _ = legs[-1]
            p["side"] = tr["side"]
            p["closed_via"] = ("stop" if abs(px - tr["stop"]) < 1e-6 else
                               "target" if tr.get(target) is not None and abs(px - tr[target]) < 1e-6 else "other")
            p["exit_adx"] = float(adx.iloc[j])
            p["signal_time"] = df.index[i].time()
            p["index"] = key
            p["t1_exit"] = how == "stop" and t1_done
            rows.append(p)
        rows.sort(key=lambda r: r["when"])
        out[vkey] = t1r.sequential(rows, config.REENTRY_COOLDOWN_MIN, config.ADX_TREND_THRESHOLD, waiver)
    return key, out


def main():
    from concurrent.futures import ProcessPoolExecutor
    import bollinger_vote_study as bvs
    import stop_day_study as sds
    with ProcessPoolExecutor(max_workers=2) as ex:
        got = dict(ex.map(_job, RULES))
    for k in RULES:
        a, b = bvs.BASELINE[k]
        s = sds.split(got[k]["t2"])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k}: T2 must reproduce the baseline"
    pool = lambda v: sorted([r for k in RULES for r in got[k][v]], key=lambda r: r["when"])
    live = sds.split(pool("t2re"))
    assert (round(live["is"]["total"]), round(live["oos"]["total"])) == (303873, 197048), \
        "T2 + re-entry must reproduce t1_reentry_study's A"
    W = 172
    print("=" * W)
    print(" OUT AT T3 vs T2, WITH THE T1-EXIT RE-ENTRY (Sensex + Midcap). Per lot, after costs.  columns: trades, won,"
          " total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for vkey, *_, label in VARIANTS:
        print(sds.line("   " + label, pool(vkey)))
    for k in RULES:
        print(f"   {k}")
        for vkey, *_, label in VARIANTS:
            print(sds.line("     " + label, got[k][vkey]))
    print("\n VERDICT - the candidate against what runs now, kept only if more in BOTH periods")
    print("  " + sds.verdict("T3 + T1 re-entry vs T2 + T1 re-entry", pool("t3re"), pool("t2re")).strip())
    print("  " + sds.verdict("(info) T3 vs T2, neither with the re-entry", pool("t3"), pool("t2")).strip())
    return got


if __name__ == "__main__":
    main()
