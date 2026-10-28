#!/usr/bin/env python3
"""
honest_retest.py — everything the tool runs on the Indian indices, re-tested with stops that fill honestly
================================================================================
The user, 28 Oct 2026: "fix the backtest and re-test everything". fills.py (same day): the rules' walks filled every
stop at the stop's own price, even when the candle opened already past it - a coin flip made +7.3M over six years.
Fixed in t3_exit_recheck_study.sim, opening_window_study.simulate_live, pro_study / reversal_exit_study /
t1_reentry_study's walks, indian_strategies_study.walk and backtest_intraday.run; honest_fill_test.py keeps it fixed.
The TradingView-rating / Trend Rider walk (tv_ratings_study) already filled at the open.

SIX YEARS (Aug 2020 -; Midcap from its launch), 15-minute candles for every system (the TradingView / Trend Rider walk
too - its 5-minute candles exist for three years only), at the money on the real expiry, 0.25% slippage, every charge,
per lot, one ticket at a time per index. First period to 15 Aug 2025, then held out. FIXED BEFORE ANY RESULT:
  SYSTEMS, each index    RULES (3 of 4 votes, the gates, today's exit), ALL_AGREE (what the accounts run), COIN (the
                         rules' gates and exit, a coin for the direction - the control: it must lose), TV_RATING (the
                         rating with the Trend Rider's exit, as the tool runs it), TREND_RIDER (Nifty / Bank Nifty),
                         CAMARILLA, SUPERTREND, ORB15, ORB30, BIG_CANDLE ("K1.5 end 2R"), RULES + BIG_CANDLE,
                         TV_RATING + BIG_CANDLE
  THE RULES' EXIT        TODAY (stop to T1 at T1, to T2 at T2, Supertrend trail after T1, 2-hour breakeven, out at T3,
                         T1-exit re-entry); NO_TRAIL; NO_2H_BE; NO_REENTRY; FIXED_T3 / FIXED_T2 / FIXED_T1 (nothing
                         moves the stop); BE_2H_ONLY (only the 2-hour breakeven, out at T3); FIRST_CLOSE (the user's
                         28 Oct idea: one target T3, stop to entry at the first 15-minute close in profit);
                         TODAY_FIRST_CLOSE
  THE RULES' ENTRY       NO_GATES, NO_ROOM (ADX gate only), NO_ADX (room only)
A change is BETTER only if it makes more than what it replaces in BOTH periods, with robustness.py's walk-forward /
Monte Carlo line (stop_day_study.verdict); a system is worth running only if it beats the COIN and makes money in both.

RESULT (28 Oct 2026, history to 26 Oct 2026; honest_retest_results.txt). Per lot after costs, to 15 Aug 2025 / held out:
  THE COIN LOSES on the rules' walk (-1,430,509 / -645,992 pooled) - the walk is honest. On the TradingView walk a coin
  makes a little in the first period and loses held out (+330,448 / -208,629 and +618,432 / -272,096); the rating
  itself +1,258,879 / +357,875.
  THE RULES ARE ABOUT BREAK-EVEN: Nifty -16,621 / +4,480, Bank Nifty -109,311 / -58,126, Sensex -13,273 / +17,619,
  Midcap +39,354 / +14,000. All-agree is no better pooled (+718 / -28,419). The gates earn their keep (no gates
  -804,363 / -353,106 against today; ADX gate only -240,199 / -98,437; room only -90,277 / +1,303). T1 re-entry
  helps (without it -44,588 / -43,953); the Supertrend trail does nothing (-4,503 / +4,848).
  THE RULES' EXIT IS WHAT LOSES: nothing moving the stop, out at T3, beats today on every index in both periods
  (pooled +281,806 / +144,348, 10 of 13 half-years); only the 2-hour breakeven, out at T3, +239,135 / +196,172 (9 of
  13). The T1 / T2 stop steps only looked best because the old fill paid them. The user's first-close move: worse than
  the plain fixed stop by -124,871 / -115,641 pooled.
  SYSTEMS THAT MAKE MONEY HONESTLY: the TradingView rating (Nifty +372,208 / +134,754, Bank Nifty +451,200 / +33,674,
  Sensex +384,473 / +85,684, Midcap +50,998 / +103,763), the Trend Rider (Nifty +242,542 / +79,378, Bank Nifty
  +320,983 / +13,556), Camarilla and Supertrend on Nifty, Sensex and Midcap (both lose Bank Nifty's held-out year),
  ORB15 / ORB30 (lose Midcap's held-out year), the big candle on all four (unchanged by the fix). Best pairs: the
  TradingView rating + the big candle (Nifty +373,982 / +142,266, Bank Nifty +500,305 / +43,221, Sensex +434,456 /
  +106,541). Camarilla, Supertrend and ORB were picked after looking (indian_strategies_study, 22 Oct) - their 2020-23
  is new data to them, but treat them as candidates, not proven.

    /usr/bin/python3 honest_retest.py          (with HOME at the six-year history)
    /usr/bin/python3 honest_retest.py tvcoin   (the TradingView walk's own coin control)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
ISS = ("CAMARILLA", "SUPERTR", "ORB15", "ORB30")
EXITS = {  # name: (target, steps, trail, 2h breakeven, first close, T1-exit re-entry)
    "TODAY": ("t3", True, True, True, False, True),
    "NO_TRAIL": ("t3", True, False, True, False, True),
    "NO_2H_BE": ("t3", True, True, False, False, True),
    "NO_REENTRY": ("t3", True, True, True, False, False),
    "FIXED_T3": ("t3", False, False, False, False, True),
    "FIXED_T2": ("t2", False, False, False, False, True),
    "FIXED_T1": ("t1", False, False, False, False, True),
    "BE_2H_ONLY": ("t3", False, False, True, False, True),
    "FIRST_CLOSE": ("t3", False, False, False, True, True),
    "TODAY_FIRST_CLOSE": ("t3", True, True, True, True, True),
}
ENTRIES = ("ALL_AGREE", "COIN_A_ONLY", "NO_GATES", "ADX_GATE_ONLY", "ROOM_ONLY")


def sim(A, i, tr, st, steps=True, trail=True, be2h=True, first_close=False, hold_bars=26):
    """t3_exit_recheck_study.sim (out at tr["t3"], step at T2) with each part switchable. -> (legs, how, T1 stepped)."""
    import fills
    import opening_window_study as ows
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    tighten = (lambda a, b: max(a, b)) if ce else (lambda a, b: min(a, b))
    past = (lambda c: c > tr["entry"]) if ce else (lambda c: c < tr["entry"])
    stop, t1_done, t2_done, be_done = tr["stop"], False, False, False
    tgt = tr.get("t3")
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(fills.stop_fill(A, j, stop, ce), j, 1.0)], "stop", t1_done and steps
        if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_done = True
            if steps:
                stop = tighten(stop, tr["t1"])
        if steps and not t2_done and ((hi[j] >= tr["t2"]) if ce else (lo[j] <= tr["t2"])):
            t2_done = True
            stop = tighten(stop, tr["t2"])
        if steps and trail and t1_done and st[j] == st[j]:
            stop = tighten(stop, st[j])
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            return [(tgt, j, 1.0)], "target", t1_done and steps
        if first_close and j == i + 1 and past(cl[j]):
            stop = tighten(stop, tr["entry"])
        if be2h and not t1_done and not be_done and (j - i) >= ows.WAIT_BARS:
            be_done = True
            if not past(cl[j]):
                return [(cl[j], j, 1.0)], "breakeven-close", t1_done and steps
            stop = tighten(stop, tr["entry"])
        if end[j]:
            return [(cl[j], j, 1.0)], "close", t1_done and steps
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)], "close", t1_done and steps


def _job(job):
    key, kind, v = job
    if kind == "entry":
        import votes_alone_study as vas
        return job, vas._job((key, v))[1]
    if kind == "bc":
        import big_candle_study as bcs
        import one_target_study as ots
        return job, bcs.run(key, ots.BC)
    if kind == "iss":
        import indian_strategies_study as iss
        return job, iss.run(key, v)
    if kind in ("tv", "tvcoin"):
        import tv_ratings_study as tvs
        tvs.FIVE_MIN = ()                            # 15-minute candles, as every other system here
        if kind == "tvcoin":                         # the control: a coin for the rating's Strong side, same walk
            import numpy as np
            real = tvs.tv_ratings

            def coin(df, real=real):
                rt = real(df).copy()
                rt["all"] = np.random.default_rng(v).choice([-0.6, 0.6], len(rt))
                return rt
            tvs.tv_ratings = coin
        _, out = tvs._tv_job(key)
        return job, {"TV_RATING": out.get("tv_ours", []), "TREND_RIDER": out.get("tool")}
    import order_block_study as ob
    import t1_reentry_study as t1r
    import t3_exit_recheck_study as t3
    target, steps, trail, be2h, first_close, reentry = EXITS[v]
    real = t3.sim
    if v == "TODAY":
        def checked(A, i, tr, st, _t, step_t2=False, **kw):
            got = real(A, i, tr, st, _t, step_t2=step_t2, **kw)
            assert got == sim(A, i, tr, st), "sim() with everything on must be t3_exit_recheck_study.sim"
            return got
        t3.sim = checked
    else:
        t3.sim = lambda A, i, tr, st, _t, step_t2=False, **kw: sim(A, i, tr, st, steps, trail, be2h, first_close)
    if not reentry:
        real_seq = t1r.sequential
        t1r.sequential = lambda rows, cd, adx, variant: real_seq(rows, cd, adx, "today")
    make = None
    if target != "t3":                               # rules_rows exits at tr["t3"]: hand it the variant's target
        make = lambda df, pre: (None, lambda i, tr: dict(tr, t3=tr[target]), None)
    return job, ob.rules_rows(key, make)


def tv_coin():
    """The TradingView / Trend Rider walk's own control: a coin (two seeds) for the Strong side, the same exit."""
    import multiprocessing as mp
    import pandas as pd
    import stop_day_study as sds
    jobs = [(k, "tvcoin", seed) for k in KEYS for seed in (1, 2)] + [(k, "tv", None) for k in KEYS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    for scope, keys in tuple((k, (k,)) for k in KEYS) + (("ALL FOUR POOLED", KEYS),):
        print("=" * 120)
        print(scope, "  the TradingView walk: the rating vs a coin  (to 15 Aug 2025 | held out)")
        print(sds.line("     TV_RATING", s([r for k in keys for r in got[(k, "tv", None)]["TV_RATING"]])))
        for seed in (1, 2):
            print(sds.line(f"     COIN (seed {seed})", s([r for k in keys for r in got[(k, "tvcoin", seed)]["TV_RATING"]])))


def main():
    import multiprocessing as mp
    import pandas as pd
    import order_block_site_study as obs
    import stop_day_study as sds
    jobs = ([(k, "tv", None) for k in KEYS] + [(k, "exit", v) for k in KEYS for v in EXITS]
            + [(k, "entry", v) for k in KEYS for v in ENTRIES] + [(k, "iss", v) for k in KEYS for v in ISS]
            + [(k, "bc", None) for k in KEYS])
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    R = {}
    for k in KEYS:
        tv = got[(k, "tv", None)]
        R[k] = {"RULES": s(got[(k, "exit", "TODAY")]), "ALL_AGREE": s(got[(k, "entry", "ALL_AGREE")]),
                "COIN": s(got[(k, "entry", "COIN_A_ONLY")]), "TV_RATING": s(tv["TV_RATING"]),
                **({"TREND_RIDER": s(tv["TREND_RIDER"])} if tv["TREND_RIDER"] is not None else {}),
                **{n: s(got[(k, "iss", n)]) for n in ISS}, "BIG_CANDLE": s(got[(k, "bc", None)])}
        R[k]["RULES + BIG_CANDLE"] = s(obs.slot(R[k]["RULES"], R[k]["BIG_CANDLE"]))
        R[k]["TV_RATING + BIG_CANDLE"] = s(obs.slot(R[k]["TV_RATING"], R[k]["BIG_CANDLE"]))
        for v in EXITS:
            R[k][f"exit {v}"] = s(got[(k, "exit", v)])
        for v in ENTRIES[2:]:
            R[k][f"entry {v}"] = s(got[(k, "entry", v)])
    pooled = {n: s([r for k in KEYS for r in R[k].get(n, [])]) for n in R["NIFTY"]}

    for scope, D in tuple((k, R[k]) for k in KEYS) + (("ALL FOUR POOLED", pooled),):
        print("=" * 120)
        print(scope, "  per lot after costs, stops filled honestly  (to 15 Aug 2025 | held out)")
        print("  SYSTEMS")
        for n in ("RULES", "ALL_AGREE", "COIN", "TV_RATING", "TREND_RIDER") + ISS + (
                "BIG_CANDLE", "RULES + BIG_CANDLE", "TV_RATING + BIG_CANDLE"):
            if n in D and (D[n] or scope != "ALL FOUR POOLED"):
                print(sds.line("     " + n, D[n]))
        print("  THE RULES' EXIT (rules entries)")
        for v in EXITS:
            print(sds.line("     " + v, D[f"exit {v}"]))
        print("  THE RULES' ENTRY (today's exit)")
        for v in ENTRIES[2:]:
            print(sds.line("     " + v, D[f"entry {v}"]))
        base = D["RULES"]
        verdicts = ([("ALL_AGREE", D["ALL_AGREE"]), ("COIN", D["COIN"])]
                    + [(f"exit {v}", D[f"exit {v}"]) for v in list(EXITS)[1:]]
                    + [(f"entry {v}", D[f"entry {v}"]) for v in ENTRIES[2:]])
        for n, rows in verdicts:
            text = sds.verdict(f"{n} vs RULES today", rows, base).strip()
            print("   " + (text.replace("\n", "\n   ") if scope == "ALL FOUR POOLED" else text.splitlines()[0]))
        for n in ("TV_RATING", "TREND_RIDER", "CAMARILLA", "SUPERTR", "BIG_CANDLE", "RULES + BIG_CANDLE",
                  "TV_RATING + BIG_CANDLE"):
            if D.get(n):
                print("   " + sds.verdict(f"{n} vs RULES", D[n], base).splitlines()[0].strip())


if __name__ == "__main__":
    tv_coin() if sys.argv[1:] == ["tvcoin"] else main()
