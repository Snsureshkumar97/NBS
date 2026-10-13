#!/usr/bin/env python3
"""
exit_ladder_study.py — the user's stop ladder for every ticket, against today's exits (3 years, the four indices)
================================================================================
The user, 13 Oct 2026: "after we enter a trade when it reaches to t1 stop loss should move to entry ltp and when it
reaches to t2 stop loss should move to t1 and when it is close to t3 the stop loss should move to t2 if it touches t3
it exit if it comes back to t2 also it exits" - for all of them.

THE LADDER, as tested (fixed before any result was seen, 13 Oct 2026):
    T1 touched                      -> the stop moves to the ENTRY
    T2 touched                      -> the stop moves to T1
    90% of the way from entry to T3 -> the stop moves to T2 ("close to T3", as the 8 Oct ladder read it)
    T3 touched                      -> out at T3;   the stop (wherever it is) -> out there;   else out by the close
  The stop only ever tightens. Inside one candle: the stop as it stood first, then T3, then the moves (from the next
  candle on).

TODAY'S EXITS, what it is measured against:
  Sensex, Midcap Select ("rules", config.SYSTEM_DEFAULTS): out at T2 (config.EXIT_AT_TARGET); at T1 the stop moves to
    T1 itself; the Supertrend trail after T1; the stop to the entry if T1 is not touched within 2 hours, priced as it
    fills - opening_window_study.simulate_live, the harness kronos_study.py and bollinger_vote_study.py use (the
    live entry checks, one position per index with the cooldown waiver, real expiries, 0.25% slippage, every charge,
    Midcap monthly); its "today" is asserted to reproduce bollinger_vote_study.BASELINE to the rupee.
  Nifty, Bank Nifty (the Trend Rider): T1/T2/T3 a third, two thirds and all of 2.75R; out at T3; 90% of the way to T2
    the stop to T1, 90% to T3 the stop to T2 (config.NEAR_TARGET_STEPS, live since 8 Oct) - trend_rider_t1_study.py's
    own walk on the 5-minute candles, the fairer test of a moving stop (its no-step walk is asserted against
    pasted_combos_study there).
  Also shown, for information: the rules indices with the ladder AND today's extras kept (Supertrend trail, 2-hour
  breakeven), and the 15-minute walk of the Trend Rider.

ADDED the same morning, while the first run was being read and before this one was run (the user: "50% of crossing
from t2 the stop loss should come at t2"): THE LADDER, HALFWAY FROM T2 - the third step at 50% of the way from T2 to
T3 instead of 90% of the way from the entry to T3; the rest the same. Judged by the same rule.

VERDICT: the ladder is KEPT for a system only if it makes more than that system's exit today in BOTH the first two
years (to 15 Aug 2025) AND the held-out final year, the system's two indices pooled. Per lot, after costs.

ADDED next, after those results and before this was run (the user: "or after it cross t1 like 20% move the stop
loss in between t1 and entry and make the exit at t2"): THE MID STOP - once the price is 20% past T1 the stop moves
to halfway between the entry and T1, out at T2; nothing else moves. "20% past T1" read two ways: (a) 20% of the way
from T1 to T2, (b) T1's distance from the entry plus 20% - the same level on the Trend Rider, whose T1 and T2 are
evenly spaced (its exit moves to its T2, two thirds of 2.75R). Judged by the same rule. (python3 exit_ladder_study.py
mid prints only these.)

RESULT (13 Oct 2026, history to 11 Sep 2026; full table in exit_ladder_study_results.txt) - DROP, both versions.
  Sensex + Midcap (rules), first two years / held-out year: today +252,225 / +186,668; the ladder +160,076 / +96,679
  (-92,149 / -89,989, worst drop 40,927 -> 81,349 held-out); halfway from T2 +154,449 / +101,866. Midcap the worst:
  held-out +86,451 -> +11,831. Keeping today's Supertrend trail and 2-hour breakeven under the ladder still loses
  (+208,824 / +153,653). Moving the stop only to the ENTRY at T1 gives back what today's stop-to-T1 keeps.
  Nifty + Bank Nifty (Trend Rider, 5-minute walk): today +339,081 / +115,033; the ladder +348,461 / +113,410;
  halfway from T2 +345,158 / +107,502 - a tie within noise (today's is the user's own 8 Oct ladder, nearly the same).
  All four: today +591,306 / +301,701; the ladder +508,537 / +210,089. Nothing changed live.
  THE MID STOP - DROP everywhere: Sensex + Midcap (a) +145,231 / +102,042, (b) +142,620 / +119,026 against
  +252,225 / +186,668, held-out worst drop 40,927 -> 113,152 / 103,843; Nifty + Bank Nifty +336,184 / +60,057 against
  +339,081 / +115,033 (Bank Nifty's held-out year +33,779 -> -15,772). It wins more often (47% vs 40%) and earns less:
  the stop halfway to T1 gives back what today's stop AT T1 keeps, and out at T2 cuts the Trend Rider's runners.

    python3 exit_ladder_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RULES = ("SENSEX", "MIDCPNIFTY")
TREND = ("NIFTY", "BANKNIFTY")
NEAR = 0.9


def ladder_exit(A, i, tr, st=None, extras=False, hold_bars=26, wait_bars=None, half_from_t2=False):
    """[(exit spot, bar, weight)] under the user's ladder (see the module docstring). extras=True keeps today's
    Supertrend trail after T1 and the 2-hour breakeven (priced as it fills), for information. half_from_t2: the third
    step (the stop to T2) at 50% of the way from T2 to T3."""
    import opening_window_study as ows
    wait_bars = ows.WAIT_BARS if wait_bars is None else wait_bars
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    tighten = (lambda a, b: max(a, b)) if ce else (lambda a, b: min(a, b))
    e, t1, t2, t3 = tr["entry"], tr["t1"], tr["t2"], tr["t3"]
    near3 = t2 + 0.5 * (t3 - t2) if half_from_t2 else e + NEAR * (t3 - e)
    stop = tr["stop"]
    t1_done = t2_done = n3_done = be_done = False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(stop, j, 1.0)]
        if (hi[j] >= t3) if ce else (lo[j] <= t3):
            return [(t3, j, 1.0)]
        if not t1_done and ((hi[j] >= t1) if ce else (lo[j] <= t1)):
            t1_done = True
            stop = tighten(stop, e)
        if not t2_done and ((hi[j] >= t2) if ce else (lo[j] <= t2)):
            t2_done = True
            stop = tighten(stop, t1)
        if not n3_done and ((hi[j] >= near3) if ce else (lo[j] <= near3)):
            n3_done = True
            stop = tighten(stop, t2)
        if extras:
            if t1_done and st is not None and st[j] == st[j]:
                stop = tighten(stop, st[j])
            if not t1_done and not be_done and (j - i) >= wait_bars:
                be_done = True
                if (cl[j] <= e) if ce else (cl[j] >= e):
                    return [(cl[j], j, 1.0)]
                stop = tighten(stop, e)
        if end[j]:
            return [(cl[j], j, 1.0)]
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)]


def mid_exit(A, i, tr, reading="a", hold_bars=26):
    """[(exit spot, bar, weight)] under the mid stop: once the price is 20% past T1 ((a) of the way to T2, (b) of T1's
    own distance from the entry) the stop moves to halfway between the entry and T1; out at T2, the stop, or the
    close. The stop as it stood first inside a candle, then T2, then the move (from the next candle on)."""
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    tighten = (lambda a, b: max(a, b)) if ce else (lambda a, b: min(a, b))
    e, t1, t2 = tr["entry"], tr["t1"], tr["t2"]
    trig = t1 + 0.2 * (t2 - t1) if reading == "a" else e + 1.2 * (t1 - e)
    mid = (e + t1) / 2
    stop, moved = tr["stop"], False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(stop, j, 1.0)]
        if (hi[j] >= t2) if ce else (lo[j] <= t2):
            return [(t2, j, 1.0)]
        if not moved and ((hi[j] >= trig) if ce else (lo[j] <= trig)):
            moved = True
            stop = tighten(stop, mid)
        if end[j]:
            return [(cl[j], j, 1.0)]
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)]


def _rules_job(job):
    """One rules index under one exit: today ("today"), the ladder ("ladder"), the ladder with today's extras."""
    key, variant = job
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
    gate = lambda i, rec: res.live_gate(key, i, rec, df)
    if variant == "today":
        return job, ows.candidates(key, df, gate, A, F, pre, st, target, True)
    # the same entries and the same pricing and position-keeping as ows.candidates, under the ladder
    out = bt.run(key, df, gate=gate)
    pos = {t: n for n, t in enumerate(df.index)}
    sig = F["sigma"].to_numpy()
    adx = pre["adx"]
    rows = []
    for tr in out["trades"]:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0 or tr.get("t3") is None:
            continue
        if variant in ("mid_a", "mid_b"):
            legs = mid_exit(A, i, tr, reading=variant[-1])
        else:
            legs = ladder_exit(A, i, tr, st, extras=(variant == "ladder+extras"), half_from_t2=(variant == "ladder50"))
        p = ps.price(key, df, A, i, tr, legs, s)
        if p is None:
            continue
        exit_px, exit_bar, _ = legs[-1]
        p["side"] = tr["side"]
        p["closed_via"] = ("stop" if abs(exit_px - tr["stop"]) < 1e-6 else
                           "target" if abs(exit_px - tr["t2" if variant.startswith("mid") else "t3"]) < 1e-6 else "other")
        p["exit_adx"] = float(adx.iloc[exit_bar])
        p["signal_time"] = df.index[i].time()
        p["index"] = key
        rows.append(p)
    rows.sort(key=lambda r: r["when"])
    return job, ccs.sequential_trades_conditional(rows, base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
                                                   waive_adx_threshold=config.ADX_TREND_THRESHOLD)


def _trend_job(k):
    """One Trend Rider index: trend_rider_t1_study's own walk with three exits - no steps (its check against
    pasted_combos_study), today's ladder (NEAR_TARGET_STEPS) and the user's."""
    import trend_rider_t1_study as trs
    RR = trs.RR
    trs.VARIANTS = [("no steps (the study's own check)", None, None, False),
                    ("today: 90% to T2 -> T1, 90% to T3 -> T2", [(RR * 2 / 3 * 0.9, RR / 3), (RR * 0.9, RR * 2 / 3)],
                     "ladder", False),
                    ("ladder: T1 -> entry, T2 -> T1, 90% to T3 -> T2",
                     [(RR / 3, 0.0), (RR * 2 / 3, RR / 3), (RR * NEAR, RR * 2 / 3)], "ladder", False),
                    ("ladder: T1 -> entry, T2 -> T1, halfway T2->T3 -> T2",
                     [(RR / 3, 0.0), (RR * 2 / 3, RR / 3), (RR * 5 / 6, RR * 2 / 3)], "ladder", False)]
    return trs._job(k)


def _trend_mid_job(k):
    """One Trend Rider index under the mid stop: its exit at T2 (two thirds of 2.75R), the stop to halfway between the
    entry and T1 (a third) once the price is 20% past T1 - trend_rider_t1_study's walk with its target set to T2."""
    import trend_rider_t1_study as trs
    R0 = trs.RR
    trs.RR = R0 * 2 / 3                                  # its one target: T2
    trs.VARIANTS = [("no steps, out at T2 (the study's own check)", None, None, False),
                    ("mid stop: 20% past T1 -> halfway entry-T1, out at T2", [(1.2 * R0 / 3, R0 / 6)], "ladder", False)]
    return trs._job(k)


def mid_main():
    """Only the mid stop, against today's exits (today's figures as the main run's)."""
    from concurrent.futures import ProcessPoolExecutor
    import bollinger_vote_study as bvs
    import stop_day_study as sds
    print("Replaying the mid stop on the four indices...", flush=True)
    jobs = [(k, v) for k in RULES for v in ("today", "mid_a", "mid_b")]
    with ProcessPoolExecutor(max_workers=6) as ex:
        rf = ex.map(_rules_job, jobs)
        tf = ex.map(_trend_job, TREND)
        mf = ex.map(_trend_mid_job, TREND)
        rules, trend, tmid = dict(rf), dict(tf), dict(mf)
    for k in RULES:
        a, b = bvs.BASELINE[k]
        s = sds.split(rules[(k, "today")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k}: 'today' must reproduce the baseline"
    pool = lambda rows: sorted(rows, key=lambda r: r["when"])
    T_TODAY, T_MID = "today: 90% to T2 -> T1, 90% to T3 -> T2", "mid stop: 20% past T1 -> halfway entry-T1, out at T2"
    r_today = pool([r for k in RULES for r in rules[(k, "today")]])
    r_a = pool([r for k in RULES for r in rules[(k, "mid_a")]])
    r_b = pool([r for k in RULES for r in rules[(k, "mid_b")]])
    t_today = pool([r for k in TREND for r in trend[k][(T_TODAY, "5")]])
    t_mid = pool([r for k in TREND for r in tmid[k][(T_MID, "5")]])
    W = 172
    print("=" * W)
    print(" THE MID STOP (20% past T1 -> the stop halfway between entry and T1, out at T2) against today's exits."
          " Per lot, after costs.")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" SENSEX + MIDCAP SELECT (the tool's rules)")
    print(sds.line("   today: T1 -> stop to T1, out at T2, trail, 2h BE", r_today))
    print(sds.line("   mid stop (a) 20% of T1->T2", r_a))
    print(sds.line("   mid stop (b) 1.2 x T1's distance", r_b))
    for k in RULES:
        print(f"   {k}")
        print(sds.line("     today", rules[(k, "today")]))
        print(sds.line("     mid stop (a)", rules[(k, "mid_a")]))
        print(sds.line("     mid stop (b)", rules[(k, "mid_b")]))
    print(" NIFTY + BANK NIFTY (the Trend Rider) - 5-minute candles")
    print(sds.line("   today: 90% to T2 -> T1, 90% to T3 -> T2, out T3", t_today))
    print(sds.line("   mid stop, out at T2", t_mid))
    for k in TREND:
        print(f"   {k}")
        print(sds.line("     today", trend[k][(T_TODAY, "5")]))
        print(sds.line("     mid stop, out at T2", tmid[k][(T_MID, "5")]))
    print(" ALL FOUR")
    print(sds.line("   today", pool(r_today + t_today)))
    print(sds.line("   mid stop (a)", pool(r_a + t_mid)))
    print("\n VERDICT - kept for a system only if it makes more than that system's exit today in BOTH periods")
    print("  " + sds.verdict("mid stop (a) on Sensex + Midcap", r_a, r_today).strip())
    print("  " + sds.verdict("mid stop (b) on Sensex + Midcap", r_b, r_today).strip())
    print("  " + sds.verdict("mid stop on Nifty + Bank Nifty", t_mid, t_today).strip())
    print("  " + sds.verdict("mid stop (a) on all four", pool(r_a + t_mid), pool(r_today + t_today)).strip())


def main():
    from concurrent.futures import ProcessPoolExecutor
    import bollinger_vote_study as bvs
    import stop_day_study as sds
    print("Replaying today's exits and the user's ladder on the four indices...", flush=True)
    jobs = [(k, v) for k in RULES for v in ("today", "ladder", "ladder50", "ladder+extras")]
    with ProcessPoolExecutor(max_workers=6) as ex:
        rf = ex.map(_rules_job, jobs)
        tf = ex.map(_trend_job, TREND)
        rules = dict(rf)
        trend = dict(tf)
    for k in RULES:
        a, b = bvs.BASELINE[k]
        s = sds.split(rules[(k, "today")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), \
            f"{k}: 'today' must reproduce bollinger_vote_study's baseline, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"

    W = 172
    T_TODAY, T_LADDER = "today: 90% to T2 -> T1, 90% to T3 -> T2", "ladder: T1 -> entry, T2 -> T1, 90% to T3 -> T2"
    T_LADDER50 = "ladder: T1 -> entry, T2 -> T1, halfway T2->T3 -> T2"
    pool = lambda rows: sorted(rows, key=lambda r: r["when"])
    sets = {
        "rules": {"today": pool([r for k in RULES for r in rules[(k, "today")]]),
                  "ladder": pool([r for k in RULES for r in rules[(k, "ladder")]]),
                  "ladder50": pool([r for k in RULES for r in rules[(k, "ladder50")]]),
                  "ladder+extras": pool([r for k in RULES for r in rules[(k, "ladder+extras")]])},
        "trend": {"today": pool([r for k in TREND for r in trend[k][(T_TODAY, "5")]]),
                  "ladder": pool([r for k in TREND for r in trend[k][(T_LADDER, "5")]]),
                  "ladder50": pool([r for k in TREND for r in trend[k][(T_LADDER50, "5")]]),
                  "today 15m": pool([r for k in TREND for r in trend[k][(T_TODAY, "15")]]),
                  "ladder 15m": pool([r for k in TREND for r in trend[k][(T_LADDER, "15")]])},
    }
    print("=" * W)
    print(" THE USER'S LADDER (T1 -> stop to entry, T2 -> stop to T1, 90% to T3 -> stop to T2, out at T3) against today's"
          " exits. Per lot, after costs.")
    print(" columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" SENSEX + MIDCAP SELECT (the tool's rules)")
    print(sds.line("   today: T1 -> stop to T1, out at T2, trail, 2h BE", sets["rules"]["today"]))
    print(sds.line("   the ladder", sets["rules"]["ladder"]))
    print(sds.line("   the ladder, halfway from T2", sets["rules"]["ladder50"]))
    print(sds.line("   (info) the ladder + today's trail and 2h BE", sets["rules"]["ladder+extras"]))
    for k in RULES:
        print(f"   {k}")
        print(sds.line("     today", rules[(k, "today")]))
        print(sds.line("     the ladder", rules[(k, "ladder")]))
        print(sds.line("     the ladder, halfway from T2", rules[(k, "ladder50")]))
    print(" NIFTY + BANK NIFTY (the Trend Rider) - 5-minute candles")
    print(sds.line("   today: 90% to T2 -> T1, 90% to T3 -> T2", sets["trend"]["today"]))
    print(sds.line("   the ladder", sets["trend"]["ladder"]))
    print(sds.line("   the ladder, halfway from T2", sets["trend"]["ladder50"]))
    print(sds.line("   (info) today, 15-minute walk", sets["trend"]["today 15m"]))
    print(sds.line("   (info) the ladder, 15-minute walk", sets["trend"]["ladder 15m"]))
    for k in TREND:
        print(f"   {k}")
        print(sds.line("     today", trend[k][(T_TODAY, "5")]))
        print(sds.line("     the ladder", trend[k][(T_LADDER, "5")]))
        print(sds.line("     the ladder, halfway from T2", trend[k][(T_LADDER50, "5")]))
    print(" ALL FOUR")
    print(sds.line("   today", pool(sets["rules"]["today"] + sets["trend"]["today"])))
    print(sds.line("   the ladder", pool(sets["rules"]["ladder"] + sets["trend"]["ladder"])))
    print(sds.line("   the ladder, halfway from T2", pool(sets["rules"]["ladder50"] + sets["trend"]["ladder50"])))

    print("\n VERDICT - kept for a system only if it makes more than that system's exit today in BOTH periods")
    for v, vname in (("ladder", "the ladder"), ("ladder50", "halfway from T2")):
        for name, title in (("rules", "Sensex + Midcap"), ("trend", "Nifty + Bank Nifty")):
            print("  " + sds.verdict(f"{vname} on {title}", sets[name][v], sets[name]["today"]).strip())
        print("  " + sds.verdict(f"{vname} on all four", pool(sets["rules"][v] + sets["trend"][v]),
                                 pool(sets["rules"]["today"] + sets["trend"]["today"])).strip())
    return rules, trend


if __name__ == "__main__":
    mid_main() if sys.argv[1:] == ["mid"] else main()
