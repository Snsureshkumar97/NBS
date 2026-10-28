#!/usr/bin/env python3
"""
best_target_study.py — one target per trade, chosen by the tool anywhere from T1 to T3
================================================================================
The user, 28 Oct 2026, on one_target_study.py: "one target means not target 3 it can be anywhere from t1 to t3 the tool
should give one target to exit in profit that should be the best target it can calculate".

THE RULE, FIXED BEFORE ANY RESULT WAS SEEN (28 Oct 2026):
  THE TICKET   one target; the rules' entries as live (order_block_study.rules_rows), per lot after costs. Three stop
               plans, each with its own learned choice:
                 first close   the user's: at the first 15-minute close after the entry, in profit -> stop to entry,
                               nothing else (one_target_study.py found this move costly on its own - kept so the
                               user's whole idea is tested)
                 fixed stop    nothing moves the stop
                 today's steps the stop to T1 at T1, to T2 at T2, the Supertrend after T1, the 2-hour breakeven
  THE LEVELS   T1, halfway T1-T2, T2, halfway T2-T3, T3.
  THE CHOICE   what the tool knows at the entry: ADX (under 25 / 25-35 / 35 and over), the time (before / after 12:00)
               and the room (T3's distance in units of the risk, under / over the median of the trades it learns from):
               12 kinds of trade. For each kind, the level that made the most on the trades it learns from (all four
               indices together; a kind with fewer than 30 trades takes the level that made the most overall).
  HOW IT LEARNS, two ways, neither seeing what it is judged on:
     SPLIT         learns on everything to 15 Aug 2025 and is judged on the held-out year after (its first-period
                   number is the one it was fitted on - shown, not judged).
     WALK-FORWARD  half-years from Aug 2020; each half-year's choice is learned on every half-year before it, from the
                   third half-year (Aug 2021) on - so five years are judged on choices made without them.
  ALSO SHOWN   every level fixed for all trades, and the hindsight best level per trade (the most it could ever add -
               not a rule, the future decides it).
BETTER only if a chooser makes more than today's three targets (out at T3) on the held-out year AND over the
walk-forward years, pooled over the four indices - and more than always using its plan's best fixed level. Run with
HOME at the six-year history.

RESULT (28 Oct 2026, history to 26 Oct 2026; best_target_study_results.txt) - DROP under every stop plan. Pooled,
walk-forward Aug 2021 to Oct 2026 (today, three targets out at T3: +900,782):
  first close     chooser  -95,921, beats today in 0 of 11 half-years (always T3 +82,520)
  fixed stop      chooser +258,543, 0 of 11 (always T3 +287,693)
  today's steps   chooser +855,360, 3 of 11 (always T3 = today); every index less (Nifty -10k, Bank Nifty -18k,
                  Sensex -7k, Midcap -10k). Learned on the five years to Aug 2025 it picks T3 for 11 of the 12 kinds
                  of trade, and the held-out year comes out +510 on 1,642 trades - the same as today.
Every fixed nearer level makes less than T3 under every plan (today's steps: T2-T3 -41,824 / -10,807, T2 -210,926 /
-45,818, T1 a loss). In hindsight the best level per trade would add +305,040 (today's steps) - but nothing the tool
knows at the entry (ADX, time, room) finds it: with the stop steps at T1 and T2, T3 already is the best single
target. (The two "+ big candle" lines in the results file were printed by a version that put all four indices in ONE
slot - they understate both sides; fixed since, not re-run. The rules-only lines above are unaffected.)

MEASURED ON THE OLD WALK (every stop filled at its own price - fills.py, 28 Oct 2026). Re-measured honestly in
honest_retest.py: the first-close move still loses against the same target with nothing moving the stop.

    /usr/bin/python3 best_target_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
LEVELS = ("T1", "T1-T2", "T2", "T2-T3", "T3")
PLANS = {"first close": (False, True), "fixed stop": (False, False), "today's steps": (True, False)}
MIN_N = 30
FIRST_JUDGED = 2                                    # walk-forward: half-years 0 and 1 only teach


def level_px(tr, lv):
    t1, t2, t3 = tr["t1"], tr["t2"], tr["t3"]
    return {"T1": t1, "T1-T2": (t1 + t2) / 2, "T2": t2, "T2-T3": (t2 + t3) / 2, "T3": t3}[lv]


def half_year(ts):
    """0 = Aug 2020 - Jan 2021, 1 = Feb - Jul 2021, ..."""
    return (ts.year * 12 + ts.month - 1 - (2020 * 12 + 7)) // 6


def feats(tr):
    return {"adx": float(tr["adx"]), "mins": tr["when"].hour * 60 + tr["when"].minute,
            "room": abs(tr["t3"] - tr["entry"]) / max(tr["risk"], 1e-9)}


def kind(f, room_cut):
    return (0 if f["adx"] < 25 else 1 if f["adx"] < 35 else 2, int(f["mins"] >= 12 * 60), int(f["room"] >= room_cut))


KINDS = [(a, t, r) for a in range(3) for t in range(2) for r in range(2)]


def fit(by_level, keep):
    """by_level {level: rows carrying feats}; learn on rows where keep(row). -> (room cut, {kind: level}, fallback)"""
    import numpy as np
    rooms = [r["room"] for r in by_level["T3"] if keep(r)]
    cut = float(np.median(rooms))
    total = {lv: sum(r["net"] for r in by_level[lv] if keep(r)) for lv in LEVELS}
    fallback = max(LEVELS, key=total.get)
    table = {}
    for k in KINDS:
        got = {lv: [r["net"] for r in by_level[lv] if keep(r) and kind(r, cut) == k] for lv in LEVELS}
        table[k] = (max(LEVELS, key=lambda lv: sum(got[lv])) if min(len(v) for v in got.values()) >= MIN_N
                    else fallback)
    return cut, table, fallback


def choose(model, tr):
    cut, table, _ = model
    return table[kind(feats(tr), cut)]


def oracle_sim(plan):
    import one_target_study as ots
    steps, first_close = PLANS[plan]

    def sim(A, i, tr, st, _t, step_t2=False, **kw):
        ce = tr["side"] == "CE"
        best = None
        for lv in LEVELS:
            got = ots.sim(A, i, dict(tr, t3=level_px(tr, lv)), st, "t3", steps, first_close)
            pts = (got[0][-1][0] - tr["entry"]) * (1 if ce else -1)
            if best is None or pts > best[0]:
                best = (pts, got)
        return best[1]
    return sim


def _job(job):
    key, what, arg, plan = job
    if what == "bc":
        import big_candle_study as bcs
        import one_target_study as ots
        return (key, what, None, None), bcs.run(key, ots.BC)
    import order_block_study as ob
    import one_target_study as ots
    import t3_exit_recheck_study as t3
    if what == "oracle":
        t3.sim = oracle_sim(plan)
    elif what != "today":
        steps, first_close = PLANS[plan]
        t3.sim = lambda A, i, tr, st, _t, step_t2=False, **kw: ots.sim(A, i, tr, st, "t3", steps, first_close)
    seen = {}

    def stop_fn(i, tr):
        f = seen[i] = feats(tr)
        if what == "level":
            lv = arg
        elif what == "split":
            lv = choose(arg, tr)
        elif what == "wf":
            m = arg.get(half_year(tr["when"]))
            lv = choose(m, tr) if m else "T3"
        else:
            return tr
        f["level"] = lv
        return dict(tr, t3=level_px(tr, lv))

    make = lambda df, pre: (None, stop_fn, lambda i, p: p.update(seen[i]))
    return (key, what, arg if what == "level" else None, plan), ob.rules_rows(key, make)


def run(jobs):
    import multiprocessing as mp
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        return dict(pool.map(_job, jobs, chunksize=1))


def main():
    import pandas as pd
    import order_block_site_study as obs
    import stop_day_study as sds
    SPLIT = sds.SPLIT
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    got = run([(k, "level", lv, p) for p in PLANS for k in KEYS for lv in LEVELS]
              + [(k, "oracle", None, p) for p in PLANS for k in KEYS]
              + [(k, w, None, None) for k in KEYS for w in ("today", "bc")])
    rows_of = lambda keys, what, arg=None, plan=None: s([r for k in keys for r in got[(k, what, arg, plan)]])
    models = {}
    for p in PLANS:
        by_level = {lv: rows_of(KEYS, "level", lv, p) for lv in LEVELS}
        hys = sorted({half_year(r["when"]) for lv in LEVELS for r in by_level[lv]})
        models[p] = (fit(by_level, lambda r: r["when"] < SPLIT),
                     {h: fit(by_level, lambda r, h=h: half_year(r["when"]) < h) for h in hys if h >= FIRST_JUDGED})
    got.update(run([(k, "split", models[p][0], p) for p in PLANS for k in KEYS]
                   + [(k, "wf", models[p][1], p) for p in PLANS for k in KEYS]))
    judged = lambda rr: [r for r in rr if half_year(r["when"]) >= FIRST_JUDGED]
    by_h = lambda rr: pd.Series([r["net"] for r in judged(rr)], index=[half_year(r["when"]) for r in judged(rr)],
                                dtype=float).groupby(level=0).sum()
    for p in PLANS:
        split_model, wf_model = models[p]
        print("#" * 120)
        print(f"STOP PLAN: {p.upper()}")
        print(f"   choice learned to 15 Aug 2025 (room cut {split_model[0]:.2f}R, fallback {split_model[2]}):  " +
              "  ".join(f"{('<25', '25-35', '35+')[k[0]]}/{'pm' if k[1] else 'am'}/{'room+' if k[2] else 'room-'}"
                        f"={split_model[1][k]}" for k in KINDS))
        print("   walk-forward fallbacks by half-year: " + " ".join(f"{h}:{m[2]}" for h, m in wf_model.items()))
        for scope, keys in (("ALL FOUR POOLED", KEYS),) + tuple((k, (k,)) for k in KEYS):
            R = {"today": rows_of(keys, "today"), "split": rows_of(keys, "split", None, p),
                 "wf": rows_of(keys, "wf", None, p), "oracle": rows_of(keys, "oracle", None, p),
                 **{lv: rows_of(keys, "level", lv, p) for lv in LEVELS}}
            print("=" * 120)
            print(f"{scope}  [{p}]  rules only, per lot after costs  (to 15 Aug 2025 | held out)")
            print(sds.line("   today: three targets, out at T3", R["today"]))
            for lv in LEVELS:
                print(sds.line(f"   one target {lv} for every trade", R[lv]))
            print(sds.line("   THE CHOOSER, learned to Aug 2025", R["split"]))
            print(sds.line("   THE CHOOSER, walk-forward", R["wf"]))
            print(sds.line("   (ceiling) hindsight best level", R["oracle"]))
            best_fixed = max(LEVELS, key=lambda lv: sum(r["net"] for r in R[lv] if r["when"] < SPLIT))
            if scope == "ALL FOUR POOLED":
                print("   " + sds.verdict("CHOOSER (learned to Aug 2025) vs today", R["split"], R["today"]).strip()
                      .replace("\n", "\n   "))
                print("   " + sds.verdict(f"CHOOSER vs always {best_fixed}", R["split"], R[best_fixed]).strip()
                      .replace("\n", "\n   "))
            ph = {n: by_h(R[n]) for n in ("today", "wf", best_fixed, "oracle")}
            hs = sorted(set(ph["today"].index) | set(ph["wf"].index))
            print(f"   walk-forward Aug 2021 on: today {ph['today'].sum():+,.0f} | chooser {ph['wf'].sum():+,.0f} | "
                  f"always {best_fixed} {ph[best_fixed].sum():+,.0f} | ceiling {ph['oracle'].sum():+,.0f} | chooser "
                  f"beats today in {sum(ph['wf'].get(h, 0) > ph['today'].get(h, 0) for h in hs)} of {len(hs)} "
                  f"half-years, always-{best_fixed} in {sum(ph['wf'].get(h, 0) > ph[best_fixed].get(h, 0) for h in hs)}")
            if scope == "ALL FOUR POOLED":
                used = pd.Series([r.get("level") for r in judged(R["wf"])]).value_counts()
                print("   walk-forward levels used:", used.to_dict())
                per_index = lambda n, arg=None, plan=None: s([r for k in KEYS for r in obs.slot(
                    rows_of((k,), n, arg, plan), rows_of((k,), "bc"))])      # one ticket at a time PER INDEX
                print(sds.line("   + big candle: today", per_index("today")))
                print(sds.line("   + big candle: THE CHOOSER (walk-forward)", per_index("wf", None, p)))


if __name__ == "__main__":
    main()
