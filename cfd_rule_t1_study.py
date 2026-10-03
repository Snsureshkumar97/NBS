#!/usr/bin/env python3
"""
cfd_rule_t1_study.py — the live Exness rules, with the stop moved to T1 once T1 is reached?
================================================================================
The user, 4 Oct 2026: "what will be the results if we do like after reaching target 1 it should
move the stop to target 1".

THE TRADES: the live entry rules (config.CFD_RULES, cfd_rules.compute - the same votes the search
scored), entered at each qualifying 15-minute close, stop at 3 x ATR, the target at 0.75 x the stop
distance; T1 and T2 are the card's waypoints at 1/3 and 2/3 of the way (0.25 and 0.5 x the stop).
Walked on Exness's REAL ticks (cfd_tick_study.walk: the stop fills at the first tick through it,
the target at the target), out after 96 bars, Exness's spread in and out and its overnight swap
on buys; one position at a time, 20 minutes after an exit (as live).

  today      the stop never moves (what is live)
  stop->T1   when T1 is touched the stop moves to T1 (then T1 or better is locked in)
  stop->T2   when T2 is touched the stop moves to T2

    python3 cfd_rule_t1_study.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

VARIANTS = {"today (stop never moves)": None, "stop -> T1 at T1": 1 / 3, "stop -> T2 at T2": 2 / 3}
COOLDOWN = pd.Timedelta(minutes=20)


BAR_NS = 15 * 60 * 10 ** 9


def walk_t(T, M, S, bar_start, cl, i, side, entry, stop, t1, t2, ratchet, max_bars=96):
    """cfd_tick_study.walk (no trail, no breakeven, no reversal) that also returns the exit's own
    TIME - the 20-minute cooldown runs from the exact exit, as live (tickets' last_close_at) and as
    cfd_vote_search did; counting it from the exit candle's close shifts which signals are taken."""
    ce = side == "CE"
    t1_done = False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + max_bars, n)):
        a, b = np.searchsorted(T, bar_start[j]), np.searchsorted(T, bar_start[j] + BAR_NS)
        mid = M[a:b]
        k0 = 0
        while k0 < len(mid):
            seg = mid[k0:]
            hit = np.flatnonzero(seg <= stop) if ce else np.flatnonzero(seg >= stop)
            s_hit = int(hit[0]) if len(hit) else None
            if ratchet and not t1_done and t1 is not None:
                u = np.flatnonzero(seg >= t1) if ce else np.flatnonzero(seg <= t1)
                u_hit = int(u[0]) if len(u) else None
                if s_hit is not None and (u_hit is None or s_hit < u_hit):
                    q = a + k0 + s_hit
                    return seg[s_hit], j, "stop", S[q], T[q]
                if u_hit is None:
                    break
                t1_done = True
                stop = max(stop, t1) if ce else min(stop, t1)
                k0 += u_hit
                continue
            g = (np.flatnonzero(seg >= t2) if ce else np.flatnonzero(seg <= t2)) if t2 is not None else []
            g_hit = int(g[0]) if len(g) else None
            if s_hit is not None and (g_hit is None or s_hit < g_hit):
                q = a + k0 + s_hit
                return seg[s_hit], j, "stop", S[q], T[q]
            if g_hit is not None:
                q = a + k0 + g_hit
                return t2, j, "target", S[q], T[q]
            break
    j = min(i + max_bars, n - 1)
    return cl[j], j, "other", np.nan, bar_start[j] + BAR_NS


def _job(args):
    sym, y, m, rows = args
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as t
    import exness_data as ed
    full = ed.load(sym)
    df = full[["Open", "High", "Low", "Close", "Volume"]]
    parts = [p for p in (t._month_ticks(sym, y, m), t._month_ticks(sym, *((y + 1, 1) if m == 12 else (y, m + 1))))
             if p is not None]
    if not parts or not rows:
        return []
    T = np.concatenate([p[0] for p in parts]); M = np.concatenate([p[1] for p in parts])
    S = np.concatenate([p[2] for p in parts])
    cl, sp_c = df["Close"].to_numpy(), full["spread_close"].to_numpy()
    bar_start = df.index.tz_convert("UTC").asi8
    out = []
    for i, side, atr in rows:
        R = 3.0 * atr
        sgn = 1 if side > 0 else -1
        entry, stop, target = cl[i], cl[i] - sgn * R, cl[i] + sgn * 0.75 * R
        for name, frac in VARIANTS.items():
            t1 = None if frac is None else cl[i] + sgn * 0.75 * R * frac
            px, j, via, xs, tx = walk_t(T, M, S, bar_start, cl, i, "CE" if side > 0 else "PE", entry, stop, t1, target,
                                        ratchet=frac is not None)
            xs = sp_c[j] if not (xs == xs) else xs
            out.append({"variant": name, "i": int(i), "j": int(j), "side": int(side), "via": via,
                        "pts": sgn * (px - entry) - sp_c[i] / 2 - xs / 2,
                        "when": df.index[i] + pd.Timedelta(minutes=15),
                        "exit_time": pd.Timestamp(int(tx), tz="UTC").tz_convert(df.index.tz)})
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_rules
    import cfd_tick_study as t
    import config
    import crypto_strategy_study as cs
    import exness_data as ed
    for sym, key, mult in (("BTCUSD", "BTC", 1.0), ("XAUUSD", "GOLD", 100.0)):
        full = ed.load(sym)
        a = cfd_rules.compute(full[["Open", "High", "Low", "Close", "Volume"]])
        plan = config.CFD_RULES[key]
        vs = np.stack([a[v] for v in plan["votes"]])
        side = np.where(np.all(vs == vs[0], axis=0) & (vs[0] != 0), vs[0], 0)
        for f in plan["filters"]:
            side = np.where(a[f], side, 0)
        # only bars with a live price at the close (as live and as the search: last tick within 10 min)
        O = np.load(os.path.join(ed._dir(), f"outcomes_{sym}.npz"))
        side = np.where(O["ok"], side, 0)
        cand = np.flatnonzero(side != 0)
        bs = full.index.tz_convert("UTC")
        by_month = {}
        for i in cand:
            e = bs[i] + pd.Timedelta(minutes=15)
            by_month.setdefault((e.year, e.month), []).append((int(i), int(side[i]), float(a["atr"][i])))
        jobs = [(sym, y, m, by_month.get((y, m), [])) for (y, m) in ed.months_until(t.LAST)]
        rows = []
        with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
            for got in ex.map(_job, jobs):
                rows.extend(got)
        long_sw, short_sw, triple = t.SWAP[sym]
        print(f"\n{'=' * 118}\n {sym} - the live rule ({' + '.join(plan['votes'])}; {' + '.join(plan['filters'])}), "
              f"stop 3 ATR, target 0.75 x; $ per {'1 BTC' if key == 'BTC' else 'gold lot'}, real ticks, spread + swap\n{'=' * 118}")
        print(f" {'exit':28s} {'IN-SAMPLE  n   win%   net        PF   maxDD':>46s}   {'HELD-OUT  n   win%   net        PF   maxDD':>46s}")
        res = {}
        for name in VARIANTS:
            vr = sorted((r for r in rows if r["variant"] == name), key=lambda r: r["when"])
            taken, free = [], None
            for r in vr:                                   # one at a time, 20 minutes after an exit (no waiver)
                if free is not None and r["when"] < free:
                    continue
                taken.append(r)
                free = r["exit_time"] + COOLDOWN
            for r in taken:
                k = t.rollovers(r["when"], r["exit_time"], triple)
                r["net"] = (r["pts"] + k * (long_sw if r["side"] > 0 else short_sw) / mult) * mult
            p = cs.periods(taken)
            res[name] = p

            def c(rs):
                s = cs.stats(rs, "net")
                return f"{s['n']:>5} {s['win']:>5.1f}% {s['total']:>+11,.0f} {s['pf']:>5.2f} {s['dd']:>9,.0f}"
            print(f" {name:28s} {c(p['is']):>46s}   {c(p['oos']):>46s}")
        b = res["today (stop never moves)"]
        bi, bo = cs.stats(b["is"], "net")["total"], cs.stats(b["oos"], "net")["total"]
        print("\n vs today (KEEP = better net in BOTH periods):")
        for name in list(VARIANTS)[1:]:
            p = res[name]
            ai, ao = cs.stats(p["is"], "net")["total"], cs.stats(p["oos"], "net")["total"]
            print(f"   {name:28s} in-sample {ai - bi:>+10,.0f}   held-out {ao - bo:>+10,.0f}   "
                  f"-> {'KEEP' if ai > bi and ao > bo else 'drop'}")


if __name__ == "__main__":
    main()
