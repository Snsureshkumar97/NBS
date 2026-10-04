#!/usr/bin/env python3
"""
cfd_strict_search.py — the vote search again, judged against CHANCE, for Exness BTC and gold
================================================================================
Why: the user, 4 Oct 2026, asked how a candle colour that flips every 15 minutes can help. It
does not: on every BTC bar, trading WITH the candle won 56.5%, AGAINST it 57.1%. And the two live
rules (cfd_vote_search.py) failed a coin-flip check - same bars, one trade at a time, direction
by coin flip: in the held-out year 20% (BTC) and 26% (gold) of coin flips did as well. Their
in-sample profit was a product of being picked, out of ~1,200 combinations, by ONE-AT-A-TIME
profit - a path a few lucky trades can make (the BTC rule loses -$24 per signal over ALL its
in-sample signals, yet +$30 per trade on the sequence the simulator happened to take).
The user: "yes run the stricter search".

THE SAME SPACE as cfd_vote_search.py: its 22 votes and 7 filters, its 28 stop/target pairs, the
outcome of every bar read off cfd_outcomes.py's tick-by-tick table (Exness's real ticks and
spread), Exness's swap, one position at a time 20 minutes after an exit.

STRICTER, in three ways - all fixed BEFORE the held-out year is looked at:
 1. CHOSEN ON EVERY SIGNAL, NOT ON ONE LUCKY PATH: a rule is scored in-sample on the trade of
    EVERY bar it fires on (each its own trade, overlapping) - the average result of its signal,
    which no sequencing can flatter - as a day-by-day t-statistic (the daily sums of those
    trades: how consistently its signals make money, not just how much on average).
 2. FEW, FIXED FINALISTS: the K best rules in-sample (each at its best exit) - and only those -
    go to the held-out year.
 3. PASS = in the held-out year, ALL of:
      (a) one trade at a time, as live: makes money after spread and swap;
      (b) every signal's trade, on average: makes money (no path flattering it);
      (c) beats 1 - 0.05/K of N_FLIPS coin flips - the same bars, one trade at a time, the
          direction by coin flip (Bonferroni: K finalists are tested, so a 5% test each would
          pass one by luck now and then).
    Also shown, not a gate: the same against SHUFFLED directions (its own buy/sell mix,
    shuffled) - a rule that passes the coin flip but not this may simply have been long in a
    rising market.
 A POSITIVE CONTROL - a rule that cheats, its direction read off the future - must pass, or the
 test cannot tell an edge from none.

    python3 cfd_strict_search.py
    python3 cfd_strict_search.py --close     the CLOSE-target table (cfd_outcomes.py --close: targets
                                             0.1 - 0.33 x the stop) and only exits that WIN at least
                                             80% in-sample - the user, 4 Oct 2026: "you didnt find
                                             anything that can make the win rate above 80% even it can
                                             give only one target to exit". Also prints what RANDOM
                                             entries do at each close target: the win rate the target
                                             alone gives, and what it nets after the spread.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cfd_vote_search as vs

BEAM = 6
MAX_PARTS = 5
MIN_BARS = 1000          # in-sample signals (bars), so a score is not a handful of lucky bars
K = 10                   # finalists per market tested on the held-out year
N_FLIPS = 2000
ALPHA = 0.05
DAY_NS = 86400 * 10 ** 9


def main():
    import pickle
    close = "--close" in sys.argv
    table, min_win = ("outcomes_close", 0.80) if close else ("outcomes", 0.0)
    import cfd_tick_study as cts
    import config
    import exness_data as ed
    rng = np.random.default_rng(20261004)
    report = {}
    for sym, (key, mult, gate) in {"BTCUSD": ("BTC", 1.0, 20), "XAUUSD": ("GOLD", 100.0, 25)}.items():
        full = ed.load(sym)
        O = np.load(os.path.join(ed._dir(), f"{table}_{sym}.npz"))
        net, ext, ok = O["net"], O["ext"], O["ok"]
        stops, targets = list(O["stops"]), list(O["targets"])
        with open(os.path.join(ed._dir(), f"adx_{sym}_{gate}_{len(full)}.pkl"), "rb") as fh:
            engine_bias = pickle.load(fh)["opp"]
        V, F = vs.votes_and_filters(full, key, engine_bias)
        entry_ns = full.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
        long_sw, short_sw, triple = cts.SWAP[sym]
        rolls, cumw = vs.rollover_weights(entry_ns[0], entry_ns[-1], triple)
        sim = vs.Sim(net, ext, ok, entry_ns, long_sw, rolls, cumw, mult)
        ins = entry_ns < vs.SPLIT
        oos = ~ins
        day = entry_ns // DAY_NS
        names_v, names_f = sorted(V), sorted(F)

        def signal(parts):
            vv = [V[p] for p in parts if p in V]
            ff = [F[p] for p in parts if p in F]
            if not vv:
                return None
            agree = np.all(np.stack([x == vv[0] for x in vv]), axis=0) & (vv[0] != 0) & ok
            for f in ff:
                agree &= f
            return agree, vv[0].astype(np.int64)

        def every_signal(idx, side):
            """$ per lot of each signal's own trade, every exit at once: (bars, stops, targets)."""
            sd = (side < 0).astype(np.int64)
            x = net[idx, sd].astype(float)                                     # (n, 4, 7)
            t1 = ext[idx, sd]
            t0 = entry_ns[idx][:, None, None]
            nights = (cumw[np.searchsorted(rolls, t1, side="right") - 1]
                      - cumw[np.searchsorted(rolls, t0, side="right") - 1])
            sw = np.where(side > 0, long_sw, short_sw).astype(float)[:, None, None]
            return (x + nights * sw / mult) * mult

        def day_t(idx, val):
            """Per exit: (mean $ per signal, day-by-day t-statistic, days)."""
            v = np.nan_to_num(val)
            _, inv = np.unique(day[idx], return_inverse=True)
            D = inv.max() + 1 if len(inv) else 0
            if D < 30:
                return None
            sums = np.zeros((D,) + v.shape[1:])
            np.add.at(sums, inv, v)
            sd = sums.std(axis=0, ddof=1)
            t = np.where(sd > 0, sums.mean(axis=0) / (sd / np.sqrt(D)), 0.0)
            return np.nanmean(val, axis=0), t, D

        if close:
            # RANDOM ENTRIES - every tradable bar, both directions averaged: the win rate the target's
            # distance alone gives, and the $ per trade it nets after the spread and swap.
            allb = np.flatnonzero(ok)
            print(f"\n  RANDOM entries (every bar, buy and sell averaged), $ per {'BTC' if key == 'BTC' else 'gold lot'}:")
            print(f"  {'exit':14s} {'IN-SAMPLE win%  $/trade':>24s}   {'HELD-OUT win%  $/trade':>24s}")
            vb = (every_signal(allb, np.ones(len(allb), np.int64)), every_signal(allb, -np.ones(len(allb), np.int64)))
            for kk in range(len(stops)):
                for jj in range(len(targets)):
                    cells = []
                    for msk in (ins[allb], oos[allb]):
                        x = np.concatenate([vb[0][msk, kk, jj], vb[1][msk, kk, jj]])
                        x = x[~np.isnan(x)]
                        cells.append(f"{100 * np.mean(x > 0):>8.1f}% {np.mean(x):>+9.1f}")
                    print(f"  {f'{stops[kk]:g}ATR/{targets[jj]:g}R':14s} {cells[0]:>24s}   {cells[1]:>24s}")

        def score(parts):
            sg = signal(parts)
            if sg is None:
                return None
            agree, side = sg
            idx = np.flatnonzero(agree & ins)
            if len(idx) < MIN_BARS:
                return None
            r = day_t(idx, every_signal(idx, side[idx]))
            if r is None:
                return None
            mean, t, D = r
            if min_win:                     # only exits that WIN at least min_win of the in-sample signals
                vals = every_signal(idx, side[idx])
                t = np.where(np.nanmean(vals > 0, axis=0) >= min_win, t, -np.inf)
                if not np.isfinite(t).any():
                    return None
            k, j = np.unravel_index(np.argmax(t), t.shape)
            return float(t[k, j]), int(k), int(j), float(mean[k, j]), len(idx)

        print(f"\n{'=' * 124}\n {sym}: {len(names_v)} votes, {len(names_f)} filters, {len(stops)} x {len(targets)} exits; "
              f"chosen in-sample (to 15 Aug 2025) on EVERY signal's trade, day by day\n{'=' * 124}", flush=True)
        seen = {}
        frontier = [(p,) for p in names_v]
        for depth in range(1, MAX_PARTS + 1):
            scored = []
            for parts in frontier:
                kp = tuple(sorted(parts))
                if kp in seen:
                    continue
                seen[kp] = score(kp)
                if seen[kp]:
                    scored.append((seen[kp][0], kp))
            scored.sort(key=lambda x: -x[0])
            if not scored:
                break
            b = seen[scored[0][1]]
            print(f"  depth {depth}: best t {b[0]:+.2f} - {' + '.join(scored[0][1])} (stop {stops[b[1]]:g} ATR, target "
                  f"{targets[b[2]]:g}R, ${b[3]:+.1f} per signal, {b[4]:,} signals)", flush=True)
            frontier = [tuple(sorted(set(p) | {x})) for _, p in scored[:BEAM] for x in names_v + names_f if x not in p]
        ranked = sorted(((s[0], p) for p, s in seen.items() if s), key=lambda x: -x[0])
        finalists = [p for _, p in ranked[:K]]

        def held_out(parts, k, j, cheat=False):
            agree, side = signal(parts)
            idx = np.flatnonzero(agree & oos)
            sd_ = side[idx]
            if cheat:                       # the positive control: the direction read off the FUTURE outcome
                sd_ = np.where(net[idx, 0, k, j] >= net[idx, 1, k, j], 1, -1)
            s = vs.stats(sim.run(idx, sd_, k, j)[1])
            ev = every_signal(idx, sd_)[:, k, j]
            flips = np.array([sim.run(idx, rng.choice([-1, 1], len(idx)), k, j)[1].sum() for _ in range(N_FLIPS)])
            shuf = np.array([sim.run(idx, rng.permutation(sd_), k, j)[1].sum() for _ in range(N_FLIPS)])
            p_coin = (1 + np.sum(flips >= s["net"])) / (1 + N_FLIPS)
            p_shuf = (1 + np.sum(shuf >= s["net"])) / (1 + N_FLIPS)
            per = float(np.nanmean(ev)) if len(ev) else float("nan")
            ok_ = s["net"] > 0 and per > 0 and p_coin <= ALPHA / K
            return {"seq": s, "per_signal": per, "p_coin": float(p_coin), "p_shuffle": float(p_shuf),
                    "coin_median": float(np.median(flips)), "pass": bool(ok_)}

        def in_seq(parts, k, j):
            agree, side = signal(parts)
            idx = np.flatnonzero(agree & ins)
            return vs.stats(sim.run(idx, side[idx], k, j)[1])

        print(f"\n  {len(seen)} rules scored. The {K} best IN-SAMPLE (fixed now), then the held-out year - PASS needs: "
              f"one-at-a-time profit > 0, every-signal average > 0, coin-flip p <= {ALPHA / K:.3f}")
        hdr = (f"  {'rule':52s} {'exit':13s} {'IS t':>5s} {'IS $/sig':>8s} {'IS one-at-a-time':>22s}   "
               f"{'HELD-OUT n  win%      net':>25s} {'$/sig':>6s} {'p coin':>7s} {'p shuf':>7s}")
        print(hdr)
        rows = []

        def line(name, parts, k, j, t_is, m_is, cheat=False):
            si = in_seq(parts, k, j)
            h = held_out(parts, k, j, cheat=cheat)
            s = h["seq"]
            print(f"  {name:52s} {f'{stops[k]:g}ATR/{targets[j]:g}R':13s} {t_is:>+5.1f} {m_is:>+8.1f} "
                  f"{si['n']:>5} {100 * si['win']:>3.0f}% {si['net']:>+10,.0f}   {s['n']:>5} {100 * s['win']:>4.0f}% "
                  f"{s['net']:>+10,.0f} {h['per_signal']:>+6.1f} {h['p_coin']:>7.4f} {h['p_shuffle']:>7.4f}  "
                  f"{'PASS' if h['pass'] else 'fail'}", flush=True)
            return {"rule": list(parts), "stop_atr": stops[k], "target_r": targets[j], "in_sample_t": t_is,
                    "in_sample_per_signal": m_is, "in_sample_seq": si, "held_out": h}

        for p in finalists:
            t_is, k, j, m_is, _ = seen[p]
            rows.append(line(" + ".join(p), p, k, j, t_is, m_is))
        # for scale: the live rule on the same tests, and the positive control
        plan = config.CFD_RULES[key]
        live = tuple(sorted(plan["votes"] + plan["filters"]))
        # the live rule's own exit - or, on the close-target table, its bars with a target at 0.25 x its stop
        k = stops.index(plan["stop_atr"])
        j = targets.index(plan["target_r"]) if plan["target_r"] in targets else targets.index(0.25)
        agree, side = signal(live)
        idx = np.flatnonzero(agree & ins)
        m, t, _ = day_t(idx, every_signal(idx, side[idx]))
        print("  " + "-" * 120)
        ref = line(("LIVE RULE: " if plan["target_r"] in targets else "LIVE RULE'S BARS, 0.25R: ") + " + ".join(live),
                   live, k, j, float(t[k, j]), float(m[k, j]))
        ctl = line("CONTROL (cheats: sees the future) on the live rule's bars", live, k, j, float("nan"), float("nan"), cheat=True)
        passed = [r for r in rows if r["held_out"]["pass"]]
        print(f"\n  {sym}: {len(passed)} of {K} finalists PASS" + (": " + "; ".join(" + ".join(r["rule"]) for r in passed)
                                                                 if passed else ""))
        if not ctl["held_out"]["pass"]:
            print("  !! the positive control did NOT pass - the test cannot be trusted")
        report[sym] = {"finalists": rows, "live_rule": ref, "control": ctl, "rules_scored": len(seen)}
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "cfd_strict_search_close_result.json" if close else "cfd_strict_search_result.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1, default=float)
    print("\nsaved", out)


if __name__ == "__main__":
    main()
