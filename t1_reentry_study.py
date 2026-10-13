#!/usr/bin/env python3
"""
t1_reentry_study.py — re-entering straight after a T1 exit (Sensex + Midcap, 3 years)
================================================================================
The user, 13 Oct 2026, after t1_stop_study.py found 41.6% of the trades closed at the T1 stop go on to T2 the same
day while every looser T1 stop earned less: "yes test the re-entry after t1 exit".

Today a second ticket the same way waits REENTRY_COOLDOWN_MIN (20 minutes) after the last exit - waived only when that
last same-direction ticket closed by REACHING ITS TARGET with ADX still at/above ADX_TREND_THRESHOLD (20)
(config.WAIVE_COOLDOWN_ON_TRENDING_TARGET, tickets._same_direction_hold). A T1 exit - T1 touched, then the stop that
moved there (or the Supertrend trail above it) hit - is a stop-out to that rule, so it always waits the 20 minutes.

FIXED BEFORE ANY RESULT WAS SEEN (13 Oct 2026). The cooldown is also waived after a same-direction T1 exit:
    A   with ADX at/above 20 at that exit - the waiver's own condition
    B   whatever the ADX
Only the clock is waived: one open position per index, the room check for a re-entry and every other entry check
apply exactly as today. Entries, the exit (opening_window_study.simulate_live), pricing and position-keeping as
bollinger_vote_study's baseline - "today" asserted to reproduce it to the rupee. KEPT only if more profit than today
in BOTH the first two years and the held-out final year, Sensex + Midcap pooled. Per lot, after costs.

RESULT (13 Oct 2026, history to 11 Sep 2026; t1_reentry_study_results.txt) - KEEP (A), built as
config.WAIVE_COOLDOWN_AFTER_T1_EXIT (tickets._same_direction_hold / _close, conditional_cooldown_live_test.py 10-11).
  Sensex + Midcap, first two years / held-out year: today +252,225 / +186,668; A +303,873 / +197,048 (+51,648 /
  +10,379; worst drop -6,980 / +4,122); B +300,688 / +197,048. The re-entries the waiver let in: 140 (69 won,
  +79,167) / 47 (20 won, +28,094). Sensex better both periods (+25,720 / +12,882); Midcap +25,928 / -2,503.

    python3 t1_reentry_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("SENSEX", "MIDCPNIFTY")
VARIANTS = ("today", "A", "B")


def sim(A, i, tr, st, target, wait_bars=None, hold_bars=26):
    """opening_window_study.simulate_live (realistic breakeven), with how it ended: ([(exit spot, bar, 1.0)], how,
    T1 touched). how: "stop" | "target" | "breakeven-close" | "close". Asserted equal to simulate_live's legs."""
    import opening_window_study as ows
    wait_bars = ows.WAIT_BARS if wait_bars is None else wait_bars
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    stop, t1_done, be_done = tr["stop"], False, False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(stop, j, 1.0)], "stop", t1_done
        if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_done = True
            stop = max(stop, tr["t1"]) if ce else min(stop, tr["t1"])
        if t1_done and st[j] == st[j]:
            stop = max(stop, st[j]) if ce else min(stop, st[j])
        tgt = tr.get(target)
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            return [(tgt, j, 1.0)], "target", t1_done
        if not t1_done and not be_done and (j - i) >= wait_bars:
            be_done = True
            if (cl[j] <= tr["entry"]) if ce else (cl[j] >= tr["entry"]):
                return [(cl[j], j, 1.0)], "breakeven-close", t1_done
            stop = max(stop, tr["entry"]) if ce else min(stop, tr["entry"])
        if end[j]:
            return [(cl[j], j, 1.0)], "close", t1_done
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)], "close", t1_done


def sequential(candidates, base_cooldown_min, adx_min, variant):
    """conditional_cooldown_study.sequential_trades_conditional, with the waiver widened per the variant (see the
    module docstring). "today" is that function exactly (asserted)."""
    import pandas as pd
    open_until = None
    last = {"CE": None, "PE": None}
    kept = []
    for tr in candidates:
        if open_until is not None and tr["when"] < open_until:
            continue
        prior = last[tr["side"]]
        if prior is not None:
            waived = prior["closed_via"] == "target" and prior["exit_adx"] >= adx_min
            if variant in ("A", "B") and prior["t1_exit"]:
                waived = waived or variant == "B" or prior["exit_adx"] >= adx_min
            cooldown = pd.Timedelta(minutes=0 if waived else base_cooldown_min)
            if tr["when"] < prior["close"] + cooldown:
                continue
            tr = dict(tr, reentry_after_t1=bool(prior["t1_exit"]) and tr["when"] < prior["close"] + pd.Timedelta(
                minutes=base_cooldown_min))
        kept.append(tr)
        open_until = tr["exit_time"]
        last[tr["side"]] = {"close": tr["exit_time"], "closed_via": tr["closed_via"], "exit_adx": tr["exit_adx"],
                            "t1_exit": tr["t1_exit"]}
    return kept


def _job(key):
    import math
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import conditional_cooldown_study as ccs
    import index_candidates_study as ics
    import indicators as ind
    import opening_window_study as ows
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    ps.SLIP = 0.0025
    if key == "MIDCPNIFTY":
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    F = rs.features(df, vix, nrv)
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
         "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(), "days": set(df.index.date)}
    pre = bt.precompute(df, key)
    st = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    target = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    out = bt.run(key, df, gate=lambda i, rec: res.live_gate(key, i, rec, df))
    pos = {t: n for n, t in enumerate(df.index)}
    sig = F["sigma"].to_numpy()
    adx = pre["adx"]
    rows = []
    for tr in out["trades"]:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        legs, how, t1_done = sim(A, i, tr, st, target)
        assert legs == ows.simulate_live(A, i, tr, st, target, realistic_be=True), "sim must be today's exit"
        p = ps.price(key, df, A, i, tr, legs, s)
        if p is None:
            continue
        px, j, _ = legs[-1]
        p["side"] = tr["side"]
        p["closed_via"] = "stop" if abs(px - tr["stop"]) < 1e-6 else "target" if abs(px - tr[target]) < 1e-6 else "other"
        p["exit_adx"] = float(adx.iloc[j])
        p["signal_time"] = df.index[i].time()
        p["index"] = key
        p["t1_exit"] = how == "stop" and t1_done           # T1 touched, then the stop that moved there (or above) hit
        rows.append(p)
    rows.sort(key=lambda r: r["when"])
    got = {v: sequential(rows, config.REENTRY_COOLDOWN_MIN, config.ADX_TREND_THRESHOLD, v) for v in VARIANTS}
    ref = ccs.sequential_trades_conditional(rows, base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
                                            waive_adx_threshold=config.ADX_TREND_THRESHOLD)
    assert [r["when"] for r in ref] == [r["when"] for r in got["today"]], "today must be the live waiver exactly"
    return key, got


def main():
    from concurrent.futures import ProcessPoolExecutor
    import bollinger_vote_study as bvs
    import regime_study as rs
    import stop_day_study as sds
    with ProcessPoolExecutor(max_workers=2) as ex:
        got = dict(ex.map(_job, RULES))
    for k in RULES:
        a, b = bvs.BASELINE[k]
        s = sds.split(got[k]["today"])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k}: today must reproduce the baseline"
    pool = lambda v: sorted([r for k in RULES for r in got[k][v]], key=lambda r: r["when"])
    label = {"today": "today: waived only after a trending target",
             "A": "A: also after a T1 exit, ADX >= 20",
             "B": "B: also after any T1 exit"}
    W = 172
    print("=" * W)
    print(" RE-ENTRY STRAIGHT AFTER A T1 EXIT (Sensex + Midcap). Per lot, after costs. columns: trades, won, total, profit"
          " factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for v in VARIANTS:
        print(sds.line("   " + label[v], pool(v)))
    for k in RULES:
        print(f"   {k}")
        for v in VARIANTS:
            print(sds.line("     " + label[v], got[k][v]))
    print("\n THE RE-ENTRIES THE WAIVER LET IN (trades taken inside the 20 minutes after a same-way T1 exit):")
    for v in ("A", "B"):
        for name, sel in (("first two years", lambda r: r["when"] < rs.SPLIT), ("held-out year", lambda r: r["when"] >= rs.SPLIT)):
            re_ = [r for r in pool(v) if r.get("reentry_after_t1") and sel(r)]
            won = sum(1 for r in re_ if r["net"] > 0)
            print(f"   {label[v]:40s} {name:16s} {len(re_):4d} re-entries, {won:4d} won, net {sum(r['net'] for r in re_):>+10,.0f}")
    print("\n VERDICT - kept only if more profit than today in BOTH periods")
    for v in ("A", "B"):
        print("  " + sds.verdict(label[v], pool(v), pool("today")).strip())
    return got


if __name__ == "__main__":
    main()
