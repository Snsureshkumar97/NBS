#!/usr/bin/env python3
"""
one_target_study.py — one target instead of three, and the stop to entry at the first 15-minute close in profit
================================================================================
The user, 28 Oct 2026: "instead of three targets what if the tool give one best target to reach and after it enter the
trade after 15 minute candle close if the ltp is above entry price the stop loss should move to entry price".

Today a rules ticket has three targets: the stop moves to T1 when T1 is touched and to T2 at T2, the Supertrend trails it
after T1, it moves to entry if T1 is not touched in 2 hours, and the ticket is out at T3. The big candle already has
one target (2 x its risk) and nothing moves its stop.

THE RULE, FIXED BEFORE ANY RESULT WAS SEEN (28 Oct 2026):
  ONE TARGET   the ticket aims at one target - T3, the one it is out at today - and nothing on the way (no T1 or T2
               step, no Supertrend trail, no 2-hour breakeven) moves the stop.
  FIRST CLOSE  at the close of the first 15-minute candle after the entry, if the index is beyond the entry on the
               trade's side (above for a call, below for a put), the stop moves to the entry; if not, it stays where it
               was. Nothing else moves it after that. (The index stands in for the option's LTP: an at-the-money option
               moves with the index, and 15 minutes of time decay is small next to it.)
  THE CANDIDATE   rules: ONE TARGET + FIRST CLOSE; the big candle: its 2R target + FIRST CLOSE.
  FOR INFORMATION (so the two halves of the idea can be told apart, and "the best target" is not picked by looking):
               the one target at T2, or none at all (held to the stop or 15:15); one target T3 with no first-close
               move; and today's three targets with the first-close move added.
The tool's own harness: order_block_study.rules_rows (the rules as live runs them, 15-minute walk, live room, cooldown
and the T1-exit re-entry) and big_candle_study.run ("K1.5 end 2R"), one ticket at a time per index (the rules first,
order_block_site_study.slot), the at-the-money option priced as pro_study.price does, per lot after costs. With no T1
rung there is no T1 exit to re-enter after.

BETTER only if the candidate makes more than today in BOTH periods (to 15 Aug 2025, then held out), and holds up on
robustness.py's walk-forward and Monte Carlo (stop_day_study.verdict). Run with HOME at the six-year history (from
Aug 2020; Midcap's from its launch).

RESULT (28 Oct 2026, history to 26 Oct 2026; one_target_study_results.txt) - DROP, and the first-close move is the
reason. Four indices pooled, rules + big candle, to 15 Aug 2025 / held out: today +1,233,885 / +321,181; THE IDEA
+445,936 / +79,611 (-787,949 / -241,570), beats today in 0 of 13 half-years, every index worse in both periods. Two in
five of its rules trades end "stop at entry": an ordinary pull-back to the entry after a first candle in profit (won
49% -> 27%). The move alone, added to today's three targets: -408,356 / -174,627, 0 of 13 half-years. One target T3
with nothing moving the stop: +719,390 / +224,206 (less than today, deeper worst drop). The big candle with the move
also makes less on all four. The single target is re-tested per trade (T1 to T3, chosen by the tool) in
best_target_study.py.

MEASURED ON THE OLD WALK (every stop filled at its own price - fills.py, 28 Oct 2026). Re-measured honestly in
honest_retest.py: the first-close move still loses against the same target with nothing moving the stop.

    /usr/bin/python3 one_target_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
BC = ("K1.5 end 2R", 1.5, True, "end", "2R")
RULES = (  # key, target (None = no target), today's steps, first-close move, label
    ("today", "t3", True, False, "rules, three targets (today)"),
    ("user", "t3", False, True, "rules, one target T3 + first close (THE IDEA)"),
    ("user_t2", "t2", False, True, "(info) one target T2 + first close"),
    ("user_none", None, False, True, "(info) no target + first close"),
    ("t3_only", "t3", False, False, "(info) one target T3, nothing moves the stop"),
    ("today_fc", "t3", True, True, "(info) three targets + first close"),
)


def in_profit(ce, close, entry):
    return close > entry if ce else close < entry


def sim(A, i, tr, st, target, steps=True, first_close=False, hold_bars=26):
    """t3_exit_recheck_study.sim (steps=True, step at T2: today's rules exit), with today's steps optional and the
    first-close move: after bar i+1 closes beyond the entry on the trade's side, the stop goes to the entry.
    -> ([(exit spot, bar, 1.0)], how, T1 touched and stepped to)."""
    import fills
    import opening_window_study as ows
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    tighten = (lambda a, b: max(a, b)) if ce else (lambda a, b: min(a, b))
    stop, t1_done, t2_done, be_done = tr["stop"], False, False, False
    tgt = tr.get(target) if target else None
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            moved = first_close and stop == tr["entry"] and stop != tr["stop"]
            return [(fills.stop_fill(A, j, stop, ce), j, 1.0)], "stop at entry" if moved else "stop", t1_done
        if steps:
            if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
                t1_done = True
                stop = tighten(stop, tr["t1"])
            if not t2_done and ((hi[j] >= tr["t2"]) if ce else (lo[j] <= tr["t2"])):
                t2_done = True
                stop = tighten(stop, tr["t2"])
            if t1_done and st[j] == st[j]:
                stop = tighten(stop, st[j])
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            return [(tgt, j, 1.0)], "target", t1_done
        if first_close and j == i + 1 and in_profit(ce, cl[j], tr["entry"]):
            stop = tighten(stop, tr["entry"])
        if steps and not t1_done and not be_done and (j - i) >= ows.WAIT_BARS:
            be_done = True
            if not in_profit(ce, cl[j], tr["entry"]):
                return [(cl[j], j, 1.0)], "breakeven-close", t1_done
            stop = tighten(stop, tr["entry"])
        if end[j]:
            return [(cl[j], j, 1.0)], "close", t1_done
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)], "close", t1_done


def walk_first_close(real_walk):
    """indian_strategies_study.walk with the first-close move: entered at bar `start`'s close."""
    def walk(bars, start, side, stop, target, custom_exit=None, last="15:15"):
        import indian_strategies_study as iss
        d = bars.index[start].date()
        bar_len = bars.index[1] - bars.index[0]
        entry = float(bars["Close"].iloc[start])
        ce = side == "CE"
        j = start
        for j in range(start + 1, len(bars)):
            if bars.index[j].date() != d or bars.index[j] + bar_len > iss.T(d, last):
                return float(bars["Close"].iloc[j - 1]), j - 1
            hi, lo = float(bars["High"].iloc[j]), float(bars["Low"].iloc[j])
            if stop is not None and ((lo <= stop) if ce else (hi >= stop)):
                op = float(bars["Open"].iloc[j])
                return (min(stop, op) if ce else max(stop, op)), j
            if target is not None and ((hi >= target) if ce else (lo <= target)):
                return target, j
            if custom_exit and custom_exit(j):
                return float(bars["Close"].iloc[j]), j
            if j == start + 1 and in_profit(ce, float(bars["Close"].iloc[j]), entry):
                stop = max(stop, entry) if ce else min(stop, entry)
        return float(bars["Close"].iloc[j]), j
    return walk


def _job(job):
    key, what, v = job
    if what == "bc":
        import big_candle_study as bcs
        import indian_strategies_study as iss
        if v == "fc":
            iss.walk = walk_first_close(iss.walk)
        return job, bcs.run(key, BC)
    import order_block_study as ob
    import t3_exit_recheck_study as t3
    _, target, steps, first_close, _ = next(r for r in RULES if r[0] == v)
    if v == "today":                                 # today's own walk, and this sim must be it exactly
        real = t3.sim

        def checked(A, i, tr, st, _t, step_t2=False, **kw):
            got = real(A, i, tr, st, _t, step_t2=step_t2, **kw)
            assert got == sim(A, i, tr, st, "t3"), "sim(steps=True) must be t3_exit_recheck_study.sim"
            return got
        t3.sim = checked
    else:
        t3.sim = lambda A, i, tr, st, _t, step_t2=False, **kw: sim(A, i, tr, st, "t3", steps, first_close)
    make = None
    if target != "t3":
        # rules_rows calls the exit "t3" and books a target exit against tr["t3"]; hand it the variant's target there
        make = lambda df, pre: (None, lambda i, tr: dict(tr, t3=tr.get(target) if target else None), None)
    rows = ob.rules_rows(key, make)
    if not steps:
        assert all(not r["t1_exit"] for r in rows), "no T1 rung, no T1 exit"
    return job, rows


def main():
    import multiprocessing as mp
    import pandas as pd
    import order_block_site_study as obs
    import stop_day_study as sds
    jobs = ([(k, "rules", v[0]) for k in KEYS for v in RULES] + [(k, "bc", v) for k in KEYS for v in ("today", "fc")])
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    pooled = {}
    for k in KEYS:
        print("=" * 120)
        print(k, "  (to 15 Aug 2025 | held out)")
        R = {v[0]: s(got[(k, "rules", v[0])]) for v in RULES}
        b0, b1 = s(got[(k, "bc", "today")]), s(got[(k, "bc", "fc")])
        for v in RULES:
            print(sds.line("   " + v[4], R[v[0]]))
        print(sds.line("   big candle, 2R (today)", b0))
        print(sds.line("   big candle, 2R + first close", b1))
        both = {"today": obs.slot(R["today"], b0), "user": obs.slot(R["user"], b1),
                "today_fc": obs.slot(R["today_fc"], b1), "t3_only": obs.slot(R["t3_only"], b0)}
        print(sds.line("   rules + big candle, today", both["today"]))
        print(sds.line("   rules + big candle, THE IDEA", both["user"]))
        print(sds.line("   (info) today + first close on both", both["today_fc"]))
        print(sds.line("   (info) one target T3, no first close", both["t3_only"]))
        print("   " + sds.verdict("THE IDEA vs today", both["user"], both["today"]).strip().replace("\n", "\n   "))
        print("   " + sds.verdict("(info) first close added to today", both["today_fc"], both["today"]).strip()
              .replace("\n", "\n   "))
        for name, rows in both.items():
            pooled.setdefault(name, []).extend(rows)
    print("=" * 120)
    print("ALL FOUR POOLED")
    P = {n: s(r) for n, r in pooled.items()}
    for n, label in (("today", "today"), ("user", "THE IDEA"), ("today_fc", "(info) today + first close"),
                     ("t3_only", "(info) one target T3, no first close")):
        print(sds.line("   " + label, P[n]))
    print("   " + sds.verdict("THE IDEA vs today", P["user"], P["today"]).strip().replace("\n", "\n   "))
    print("   " + sds.verdict("(info) first close added to today", P["today_fc"], P["today"]).strip()
          .replace("\n", "\n   "))
    # how the idea's trades ended, and what the first close did
    for k in KEYS:
        rows = s(got[(k, "rules", "user")])
        how = pd.Series([r.get("how") for r in rows]).value_counts().to_dict()
        print(f"   {k:11s} THE IDEA's rules exits: {how}")


if __name__ == "__main__":
    main()
