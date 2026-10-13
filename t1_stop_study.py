#!/usr/bin/env python3
"""
t1_stop_study.py — what the T1 stop costs and saves, and a stop just under T1 (Sensex + Midcap, 3 years)
================================================================================
The user, 13 Oct 2026: "in the current trades i want the exit at t2 now it exits at t1 because stop loss move to t1 as
soon as it touches it i am feeling i am loosing money after i exit t1 how to fix this give me sugggestions".

On the tool's own rules (Sensex and Midcap Select, config.SYSTEM_DEFAULTS) a ticket's stop moves to T1 the moment T1
is touched, and it exits at T2. Measured here on today's exit (opening_window_study.simulate_live - the T1 step, the
Supertrend trail, the 2-hour breakeven, out at T2; entries, pricing and position-keeping as bollinger_vote_study's
baseline, asserted to the rupee):

  1. WHAT HAPPENS AFTER A T1 EXIT. Every trade that touched T1 and then closed at its T1 stop: after that exit, the
     same day, did the index reach T2 first (the move the user feels was missed), fall back to the ORIGINAL stop first
     (the loss the T1 stop saved), or neither?
  2. A STOP JUST UNDER T1 (fixed before any result was seen): at the T1 touch the stop moves to 90%, or 75%, of the
     way from the entry to T1 instead of to T1 itself; everything else as today. (Halfway and the entry were tested
     the same morning in exit_ladder_study.py - DROP; a delay before the T1 step on 2 Oct - DROP.) KEPT only if more
     profit than today in BOTH the first two years and the held-out final year.

RESULT (13 Oct 2026, history to 11 Sep 2026; t1_stop_study_results.txt) - the feeling is real, the fix is not.
  1. Of 476 trades closed at the T1 stop in three years, 41.6% went on to T2 the same day (~99 index points more);
     9.5% fell back to the original stop (~327 points saved each); 48.9% neither. Held-out year: 38.9% / 6.0% / 55.0%.
  2. DROP both: the stop at 90% of the way to T1 -25,121 / -42,480 against today (+252,225 / +186,668); at 75%
     -50,039 / -56,033. With the entry and halfway (exit_ladder_study.py) and a delay (2 Oct), five looser T1 stops
     tested, every one made less: the trades that come back below T1 and drift give back more than the ones that go
     on to T2 add. Nothing changed live.
  3. Then (the user: "what if i remove the trailing stop loss to t1 instead it moves to entry"): the stop to the ENTRY
     at T1, all else as today - DROP, the worst of them: -52,852 / -70,340 (+199,373 / +116,329), held-out worst drop
     40,927 -> 72,707, won 40% -> 33% / 37% -> 30%. Midcap's held-out year +86,451 -> +35,242.
  4. Then (the user: "what if we remove the trailing stop and just make the exit at t2"): DROP both. No trailing (no
     T1 step, no Supertrend trail, 2-hour breakeven kept) -29,928 / -65,411; nothing moving at all (first stop, out at
     T2) -83,978 / -104,222, held-out worst drop 40,927 -> 104,581, Midcap's held-out year a loss (-6,867). It wins
     more often (43-44%) and earns far less: the trail is what keeps the profit of the trades that reach T1 and turn.

    python3 t1_stop_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("SENSEX", "MIDCPNIFTY")
SHARES = (1.0, 0.9, 0.75, 0.0)     # where the stop goes at the T1 touch: share of the way from the entry to T1
# 0.0 - the stop to the ENTRY at T1, everything else as today - added 13 Oct 2026 before it was run (the user: "what if
# i remove the trailing stop loss to t1 instead it moves to entry")
# "notrail" / "fixed" - added next, before they were run (the user: "what if we remove the trailing stop and just make
# the exit at t2"): no T1 step and no Supertrend trail, the 2-hour breakeven kept ("notrail"); or nothing moving at
# all - the first stop, out at T2, the stop or the close ("fixed")
PLAIN = ("notrail", "fixed")


def sim_plain(A, i, tr, target, keep_be=True, wait_bars=None, hold_bars=26):
    """No trailing: the first stop until T2, the stop or the close - with keep_be, the 2-hour breakeven as today
    (priced as it fills). -> ([(exit spot, bar, 1.0)], None)."""
    import opening_window_study as ows
    wait_bars = ows.WAIT_BARS if wait_bars is None else wait_bars
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    stop, t1_seen, be_done = tr["stop"], False, False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(stop, j, 1.0)], None
        if not t1_seen and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_seen = True                       # today the breakeven waits only while T1 is untouched; kept the same
        tgt = tr.get(target)
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            return [(tgt, j, 1.0)], None
        if keep_be and not t1_seen and not be_done and (j - i) >= wait_bars:
            be_done = True
            if (cl[j] <= tr["entry"]) if ce else (cl[j] >= tr["entry"]):
                return [(cl[j], j, 1.0)], None
            stop = max(stop, tr["entry"]) if ce else min(stop, tr["entry"])
        if end[j]:
            return [(cl[j], j, 1.0)], None
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)], None


def sim(A, i, tr, st, target, share=1.0, wait_bars=None, hold_bars=26):
    """opening_window_study.simulate_live (realistic breakeven) with the T1 step to `share` of the way to T1 (1.0 is
    today's, asserted equal). -> ([(exit spot, bar, 1.0)], the stop the T1 step set or None)."""
    import opening_window_study as ows
    wait_bars = ows.WAIT_BARS if wait_bars is None else wait_bars
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    stop, t1_done, be_done, t1_stop = tr["stop"], False, False, None
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(stop, j, 1.0)], t1_stop
        if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_done = True
            t1_stop = tr["entry"] + share * (tr["t1"] - tr["entry"])
            stop = max(stop, t1_stop) if ce else min(stop, t1_stop)
        if t1_done and st[j] == st[j]:
            stop = max(stop, st[j]) if ce else min(stop, st[j])
        tgt = tr.get(target)
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            return [(tgt, j, 1.0)], t1_stop
        if not t1_done and not be_done and (j - i) >= wait_bars:
            be_done = True
            if (cl[j] <= tr["entry"]) if ce else (cl[j] >= tr["entry"]):
                return [(cl[j], j, 1.0)], t1_stop
            stop = max(stop, tr["entry"]) if ce else min(stop, tr["entry"])
        if end[j]:
            return [(cl[j], j, 1.0)], t1_stop
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)], t1_stop


def _job(job):
    key, share = job
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
    dates = np.array(df.index.date)
    rows = []
    for tr in out["trades"]:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        legs, t1_stop = (sim_plain(A, i, tr, target, keep_be=(share == "notrail")) if share in PLAIN
                         else sim(A, i, tr, st, target, share))
        if share == 1.0:
            assert legs == ows.simulate_live(A, i, tr, st, target, realistic_be=True), "share 1.0 must be today's exit"
        p = ps.price(key, df, A, i, tr, legs, s)
        if p is None:
            continue
        px, j, _ = legs[-1]
        p.update(side=tr["side"], index=key, signal_time=df.index[i].time(), exit_adx=float(pre["adx"].iloc[j]))
        p["closed_via"] = "stop" if abs(px - tr["stop"]) < 1e-6 else "target" if abs(px - tr[target]) < 1e-6 else "other"
        # after a T1-stop exit: T2 first, the ORIGINAL stop first, or neither, the same day
        if t1_stop is not None and abs(px - t1_stop) < 1e-6:
            ce = tr["side"] == "CE"
            after = "neither"
            for k in range(j + 1, len(dates)):
                if dates[k] != dates[i]:
                    break
                if (A["lo"][k] <= tr["stop"]) if ce else (A["hi"][k] >= tr["stop"]):
                    after = "stop"
                    break
                if (A["hi"][k] >= tr["t2"]) if ce else (A["lo"][k] <= tr["t2"]):
                    after = "t2"
                    break
            p["t1_exit"] = after
            p["t1_to_t2"] = abs(tr["t2"] - tr["t1"])
            p["t1_to_stop"] = abs(tr["t1"] - tr["stop"])
        rows.append(p)
    rows.sort(key=lambda r: r["when"])
    return job, ccs.sequential_trades_conditional(rows, base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
                                                  waive_adx_threshold=config.ADX_TREND_THRESHOLD)


def main():
    from concurrent.futures import ProcessPoolExecutor
    import bollinger_vote_study as bvs
    import stop_day_study as sds
    with ProcessPoolExecutor(max_workers=6) as ex:
        got = dict(ex.map(_job, [(k, s) for k in RULES for s in SHARES + PLAIN]))
    for k in RULES:
        a, b = bvs.BASELINE[k]
        s = sds.split(got[(k, 1.0)])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k}: today must reproduce the baseline"
    today = sorted([r for k in RULES for r in got[(k, 1.0)]], key=lambda r: r["when"])

    print("=" * 120)
    print(" 1. AFTER A T1 EXIT (Sensex + Midcap, today's exit, 3 years): the trades that touched T1 and closed at the T1 stop")
    print("=" * 120)
    for name, sel in (("all three years", lambda r: True),
                      ("held-out final year", lambda r: r["when"] >= __import__("regime_study").SPLIT)):
        ex = [r for r in today if r.get("t1_exit") and sel(r)]
        n = len(ex)
        t2 = [r for r in ex if r["t1_exit"] == "t2"]
        stp = [r for r in ex if r["t1_exit"] == "stop"]
        nei = n - len(t2) - len(stp)
        tot = len([r for r in today if sel(r)])
        print(f"  {name}: {tot} trades, {n} closed at the T1 stop. After that exit, the same day:")
        if n:
            print(f"    went on to T2 first          {len(t2):4d}  ({100 * len(t2) / n:4.1f}%)  - the move that was missed: "
                  f"T1 -> T2 averages {sum(r['t1_to_t2'] for r in t2) / max(1, len(t2)):.0f} index points")
            print(f"    fell back to the ORIGINAL stop {len(stp):4d}  ({100 * len(stp) / n:4.1f}%)  - the loss the T1 stop saved: "
                  f"T1 -> stop averages {sum(r['t1_to_stop'] for r in stp) / max(1, len(stp)):.0f} index points")
            print(f"    neither (drifted to the close) {nei:4d}  ({100 * nei / n:4.1f}%)")

    print()
    print("=" * 172)
    print(" 2. WHERE THE STOP GOES AT THE T1 TOUCH. Per lot, after costs. columns: trades, won, total, profit factor, worst"
          " drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    label = {1.0: "today: at T1 itself", 0.9: "90% of the way to T1", 0.75: "75% of the way to T1",
             0.0: "the ENTRY (breakeven) at T1", "notrail": "no trailing at all, 2h breakeven kept",
             "fixed": "nothing moves: first stop, out at T2"}
    for s_ in SHARES + PLAIN:
        print(sds.line("   " + label[s_], sorted([r for k in RULES for r in got[(k, s_)]], key=lambda r: r["when"])))
    for k in RULES:
        print(f"   {k}")
        for s_ in SHARES + PLAIN:
            print(sds.line("     " + label[s_], got[(k, s_)]))
    print("\n VERDICT - kept only if more profit than today in BOTH periods")
    for s_ in SHARES[1:] + PLAIN:
        print("  " + sds.verdict(label[s_], sorted([r for k in RULES for r in got[(k, s_)]], key=lambda r: r["when"]),
                                 today).strip())
    return got


if __name__ == "__main__":
    main()
