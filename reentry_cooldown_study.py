#!/usr/bin/env python3
"""
reentry_cooldown_study.py — what if REENTRY_COOLDOWN_MIN were a different value?
================================================================================
The user, 2 Oct 2026, going back to the actual question that started this
whole line of work: after a ticket closes, config.REENTRY_COOLDOWN_MIN (20
minutes) blocks a fresh SAME-DIRECTION entry on that index until it elapses.
Two indirect fixes (letting the ORIGINAL trade extend instead of closing:
see nbs-partial-exit-runner-study.md and nbs-ladder-confirm-study.md) were
both DROP. This file tests the DIRECT lever instead: the cooldown length
itself.

WHY THIS NEEDED A NEW SIMULATOR, NOT JUST A NEW EXIT FUNCTION
    Every other study in this project (pro_study.py and everything built on
    it) treats each qualifying bar as an INDEPENDENT candidate trade - there
    is nothing stopping two overlapping "trades" on the same index both
    counting toward a pooled total. That is fine for comparing EXIT policies
    on the same fixed set of entries, which is all those studies ever did.
    It is NOT fine here: a cooldown only ever matters in a SEQUENTIAL world
    where a ticket must actually close before another can open, and where a
    same-direction re-entry has to wait out the cooldown clock. Testing
    different cooldown lengths against a trade list that never enforced
    exclusivity in the first place would not test anything real.

    sequential_trades() below is new: it walks each index's own candidates
    in time order and enforces what the live tool actually enforces - at
    most one open position per index, and (for a same-direction follow-up)
    at least `cooldown_min` minutes since the last one of that direction
    closed on that index. A candidate that arrives while a position is
    still open, or inside its own direction's cooldown, is simply never
    issued - exactly as tickets.py would never have issued it live.

    THIS ALSO MEANS: every total this project has published before today
    (this file's own "cooldown = 20 (today)" row included, once run through
    this simulator for the first time) may differ from the EARLIER, naive
    pooled numbers that let trades overlap. That earlier number was never a
    faithful count of what the live tool could actually have taken - this
    one is. Both are printed below so the gap itself is visible, not hidden.

    Pricing reuses pro_study.price() unmodified - entries, exits and costs
    are identical to every other exit-shaped study in this project; only
    WHICH candidates survive to be priced changes.
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs

INDICES = rs.INDICES
SPLIT = rs.SPLIT


def sequential_trades(candidates, cooldown_min):
    """candidates: one index's own trades, each a dict with "when" (entry,
    a pandas Timestamp), "exit_time" (a pandas Timestamp), and "side" ("CE"
    or "PE") - sorted by "when" on entry to this function (callers must sort;
    kept explicit rather than silently re-sorting a list that is also used
    elsewhere by its original order).

    Returns the subset the live tool would actually have been free to
    issue: at most one open position at a time, and a same-direction
    follow-up only once cooldown_min minutes have passed since the LAST
    trade of that same direction closed. An opposite-direction entry is
    never subject to this cooldown at all - config.ALLOW_SAME_DIRECTION_REENTRY
    and REENTRY_COOLDOWN_MIN both only ever govern the same direction.
    """
    open_until = None
    last_close = {"CE": None, "PE": None}
    kept = []
    cooldown = pd.Timedelta(minutes=cooldown_min)
    for tr in candidates:
        if open_until is not None and tr["when"] < open_until:
            continue          # a position is still open on this index
        lc = last_close[tr["side"]]
        if lc is not None and tr["when"] < lc + cooldown:
            continue          # same-direction cooldown still running
        kept.append(tr)
        open_until = tr["exit_time"]
        last_close[tr["side"]] = tr["exit_time"]
    return kept


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

    # One priced candidate list per index, built once - the SAME entries and
    # SAME default exit (hold to T3/stop) every other study in this project
    # uses. Every cooldown value below is tested by re-filtering THIS list,
    # never by re-running the backtest or re-pricing anything.
    per_index = {}
    naive_rows = []        # every candidate, unfiltered - today's prior methodology
    for k in INDICES:
        df = hists[k]
        res = bt.run(k, df, gate=base_gate[k])
        pos = {t: n for n, t in enumerate(df.index)}
        sig = F[k]["sigma"].to_numpy()
        rows = []
        for tr in res["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            legs = ps.simulate(A[k], i, tr)
            p = ps.price(k, df, A[k], i, tr, legs, s)
            if p:
                p["side"] = tr["side"]
                rows.append(p)
        rows.sort(key=lambda r: r["when"])
        per_index[k] = rows
        naive_rows.extend(rows)

    def stats_for(cooldown_min):
        rows = []
        for k in INDICES:
            rows.extend(sequential_trades(per_index[k], cooldown_min))
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}, rows

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:34s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("=" * 128)
    print(" All per lot, pooled across NIFTY / BANKNIFTY / SENSEX, real expiries, after costs")
    print("=" * 128)
    hdr = (f" {'variant':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print(hdr); print("-" * 128)

    naive = {"is": ps.stats([r for r in naive_rows if r["when"] < SPLIT]),
             "oos": ps.stats([r for r in naive_rows if r["when"] >= SPLIT])}
    print(line("prior methodology (overlaps allowed, no cooldown)", naive))
    print("   ^ every number this project has published before today used this - see docstring")
    print("-" * 128)

    out = {}
    for cd in (0, 5, 10, 15, 20, 30, 45, 60):
        r, _ = stats_for(cd)
        label = f"cooldown = {cd} min" + (" (today, live)" if cd == 20 else "")
        out[cd] = r
        print(line(label, r))

    base = out[20]
    print("\n VERDICT — kept only if better than TODAY'S 20-minute cooldown, both periods, "
          "with the sequential (one-open-position) simulator applied to BOTH sides")
    for cd, r in out.items():
        if cd == 20:
            continue
        di = r["is"]["total"] - base["is"]["total"]; do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        keep = di > 0 and do > 0
        print(f"   {'KEEP ' if keep else 'drop '} cooldown = {cd:>3} min   in-sample {di:>+10,.0f}"
              f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    return out


if __name__ == "__main__":
    main()
