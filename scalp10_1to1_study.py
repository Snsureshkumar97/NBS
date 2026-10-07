#!/usr/bin/env python3
"""
scalp10_1to1_study.py — the momentum entry with a 10-point target AND a 10-point stop (1:1), on 5-minute candles
================================================================================
The user, 7 Oct 2026, after scalp10_study.py (a +10 target against the signal's own stop lost in every variant):
"yes test 10 points target with 10 points stop".

The same entries as scalp10_study.py (read its header): the tool's signals with each "full momentum" rule, on the
tool's rules (A) and on today's live mix (B: the Trend Rider on Nifty and Bank Nifty, the rules on Sensex), one position
per index, the cooldown (rules), out by the close. Three exits, walked on the 3 years of 5-MINUTE candles
(~/trading-tool-logs/history/<INDEX>_5m_3y.csv, entry_timing_study.py's) from the entry's 15-minute close:
  1:1 premium   - out at +10 or -10 on the option's premium (both levels follow the time decay, as they would live)
  1:1 index     - out at +10 or -10 index points
  +10 / signal stop - scalp10_study.py's exit again, on the finer candles (a check that 15 minutes did not decide it)
Today's exit (the 15-minute harness, every study's baseline) is the yardstick.

A 10-point stop and a 10-point target are close together: a 5-minute candle can still reach both, and it does not say
which came first. Every result is given three ways - STOP first (the cautious count), TARGET first (the hopeful one), and
the MIDDLE: the candle's own path read from its shape (a candle that closed up went open-low-high-close, one that closed
down open-high-low-close). The verdict uses the middle; the other two show how much the candle question decides.

Premiums and charges exactly as scalp10_study.py (Black-Scholes on the at-the-money strike of the real expiry, 0.25%
slippage a side, every Zerodha charge); big lots with the charges at the size and an extra half point a side.

    python3 scalp10_1to1_study.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import config
import entry_timing_study as ets
import pasted_combos_study as pc
import pro_study as ps
import regime_study as rs
import scalp10_study as sc
import stop_day_study as sds

INDICES = sds.INDICES
EXITS = ("p11", "i11", "p10")
ELABEL = {"p11": "1:1 premium (+10/-10)", "i11": "1:1 index (+10/-10)", "p10": "+10 premium, signal's stop"}
READ = ("cons", "mid", "opt")
FIVE = pd.Timedelta(minutes=5)
BAR = pd.Timedelta(minutes=15)


def five_arrays(k):
    d5 = ets.load_5m(k)
    t = d5.index
    return {"t": t, "o": d5["Open"].to_numpy(), "h": d5["High"].to_numpy(), "l": d5["Low"].to_numpy(),
            "c": d5["Close"].to_numpy(),
            "day_last": (pd.Series(t.date) != pd.Series(t.date).shift(-1)).to_numpy(),
            "tod": np.array([x.time() for x in t])}


def walk5(M, p0, side, stop_at, tgt_at, cutoff_tod=None):
    """From 5-minute candle p0: (cons, mid, opt, ambiguous) - each (exit spot, candle, via). A candle reaching both the
    stop and the target: the stop (cons), the target (opt), and for mid whichever its shape says came first."""
    o, h, l, c, last, tod = M["o"], M["h"], M["l"], M["c"], M["day_last"], M["tod"]
    ce = side == "CE"
    for p in range(p0, len(c)):
        s, t = stop_at(p), tgt_at(p)
        sh = s is not None and ((l[p] <= s) if ce else (h[p] >= s))
        th = t is not None and ((h[p] >= t) if ce else (l[p] <= t))
        if sh and th:
            low_first = c[p] >= o[p]                      # closed up: open, low, high, close
            stop_first = low_first if ce else not low_first
            cons, opt = (s, p, "stop"), (t, p, "target")
            return cons, (cons if stop_first else opt), opt, True
        if sh:
            r = (s, p, "stop")
            return r, r, r, False
        if th:
            r = (t, p, "target")
            return r, r, r, False
        if last[p] or (cutoff_tod is not None and tod[p] >= cutoff_tod):
            r = (c[p], p, "close")
            return r, r, r, False
    r = (c[-1], len(c) - 1, "close")
    return r, r, r, False


def levels(k, df, A, ie, entry, side, sigma, M, kind, signal_stop):
    """(stop_at(p), target_at(p), p0 premium) for exit `kind`, or None when the option is too cheap to price."""
    K, exp, p0 = sc.contract(k, df, A, ie, entry, side, sigma)
    if p0 <= 0.5:
        return None
    call, ce = side == "CE", side == "CE"
    if kind == "i11":
        up, dn = entry + sc.POINTS, entry - sc.POINTS
        return ((lambda p: dn) if ce else (lambda p: up)), ((lambda p: up) if ce else (lambda p: dn)), p0
    lo, hi = entry * 0.85, entry * 1.15
    at = lambda p, goal: sc.solve_spot(goal, K, ps.years_to(exp, M["t"][p] + FIVE), sigma, call, lo, hi)
    tgt = lambda p: at(p, p0 + sc.POINTS)
    if kind == "p10":
        return (lambda p: signal_stop), tgt, p0
    if p0 - sc.POINTS <= 0.05:
        return None                                        # a premium under 10: no 10-point stop to set
    return (lambda p: at(p, p0 - sc.POINTS)), tgt, p0


def row(k, df, A, ie, entry, side, sigma, M, leg, adx15, pos15, amb=False):
    """A priced trade for one reading's exit leg (spot, 5-minute candle, via)."""
    K, exp, p0 = sc.contract(k, df, A, ie, entry, side, sigma)
    spot, p, via = leg
    t_out = M["t"][p] + FIVE
    p1 = rs.bs(spot, K, ps.years_to(exp, t_out), sigma, side == "CE")
    meta = config.INSTRUMENTS[k]
    q = {"p0": p0, "p1": p1, "qty": meta["lot_size"], "exch": "BSE" if meta["kite_exchange"] == "BSE" else "NSE",
         "index": k, "when": df.index[ie] + BAR, "exit_time": t_out}
    bar = pos15.get((t_out - FIVE).floor("15min"))
    return dict(q, side=side, closed_via=via, net=sc.net(q), amb=bool(amb),
                exit_adx=float(adx15[bar]) if bar is not None else 0.0)


def _job(k):
    """{(system, momentum, exit, reading): rows} for index k (the rules raw, before the walk; the Trend Rider walked)."""
    c = sds._context()
    df, A, pre = c["hists"][k], c["A"][k], c["pre"][k]
    P, sig, adx = sc._arrays(df, pre), c["F"][k]["sigma"].to_numpy(), pre["adx"].to_numpy()
    pos15 = {t: n for n, t in enumerate(df.index)}
    M = five_arrays(k)
    t5 = M["t"]
    out = {}

    def start(ie):
        p = int(t5.searchsorted(df.index[ie] + BAR))
        return p if p < len(t5) and t5[p].date() == df.index[ie].date() else None

    # A: the rules' entries
    trades = bt.run(k, df, gate=c["live"][k])["trades"]
    for tr in trades:
        i = pos15[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        for m in sc.MOMENTUM:
            got = sc.momentum(m, i, tr["side"], tr["stop"], A, P)
            if got is None:
                continue
            ie, entry = got
            if ie == i:
                entry = tr["entry"]
            p0 = start(ie)
            if p0 is None:
                continue
            for e in EXITS:
                lv = levels(k, df, A, ie, entry, tr["side"], s, M, e, tr["stop"])
                if lv is None:
                    continue
                legs = walk5(M, p0, tr["side"], lv[0], lv[1])
                for rd, leg in zip(READ, legs[:3]):
                    out.setdefault(("rules", m, e, rd), []).append(
                        row(k, df, A, ie, entry, tr["side"], s, M, leg, adx, pos15, legs[3]))

    # B: the Trend Rider, walked one position at a time as it runs live
    tp = config.TREND_RIDER
    long_, short = pc.signals(df[["Open", "High", "Low", "Close", "Volume"]], 2, tp["adx_min"])
    tod = np.array([t.time() for t in df.index])
    date = np.array([t.date() for t in df.index])
    last = (pd.Timestamp("2000-01-01 15:15") - BAR).time()
    cut5 = (pd.Timestamp("2000-01-01 15:15") - FIVE).time()             # the 5-minute candle that ends 15:15
    for m in sc.MOMENTUM:
        for e in EXITS:
            for rd_i, rd in enumerate(READ):
                rows, free_at = out.setdefault(("tr", m, e, rd), []), None
                for i in range(60, len(df) - 1):
                    if free_at is not None and df.index[i] < free_at:
                        continue
                    side = "CE" if long_[i] and not long_[i - 1] else "PE" if short[i] and not short[i - 1] else None
                    if side is None or tod[i] >= last or date[i + 1] != date[i]:
                        continue
                    s = sig[i]
                    if not (s == s) or s <= 0:
                        continue
                    w = tp["swing"]
                    stop = A["lo"][max(0, i - w + 1):i + 1].min() if side == "CE" else A["hi"][max(0, i - w + 1):i + 1].max()
                    got = sc.momentum(m, i, side, stop, A, P)
                    if got is None:
                        continue
                    ie, entry = got
                    if tod[ie] >= last:
                        continue
                    p0 = start(ie)
                    if p0 is None:
                        continue
                    lv = levels(k, df, A, ie, entry, side, s, M, e, stop)
                    if lv is None:
                        continue
                    legs = walk5(M, p0, side, lv[0], lv[1], cutoff_tod=cut5)
                    r = row(k, df, A, ie, entry, side, s, M, legs[rd_i], adx, pos15, legs[3])
                    rows.append(r)
                    free_at = r["exit_time"].ceil("15min")
    return k, out


def main():
    from concurrent.futures import ProcessPoolExecutor
    print("Replaying every entry with the 1:1 exits on 5-minute candles, all three indices...", flush=True)
    with ProcessPoolExecutor(max_workers=len(INDICES)) as ex:
        got = {k: o for k, o in ex.map(_job, INDICES)}
    # today's exit, the yardstick: every study's baseline (scalp10_study.py's own replay reproduces it)
    today = {}
    with ProcessPoolExecutor(max_workers=len(INDICES)) as ex:
        sc_got = {k: (ru, trr) for k, ru, trr in ex.map(sc._job, INDICES)}
    for m in sc.MOMENTUM:
        a = sds.per_index([r for k in INDICES for r in sc_got[k][0][(m, "today")]])
        b = [r for r in a if config.SYSTEM_DEFAULTS.get(r["index"], "rules") == "rules"]
        b += [r for k in INDICES if config.SYSTEM_DEFAULTS.get(k) == "trend_rider" for r in sc_got[k][1][(m, "today")]]
        today[("rules", m)], today[("live", m)] = a, sorted(b, key=lambda r: r["when"])
    base = sds.split(today[("rules", "signal")])
    assert (round(base["is"]["total"]), round(base["oos"]["total"])) == (437509, 125227)

    def rows_for(setup, m, e, rd):
        rules = sds.per_index([r for k in INDICES for r in got[k].get(("rules", m, e, rd), [])])
        if setup == "rules":
            return rules
        live = [r for r in rules if config.SYSTEM_DEFAULTS.get(r["index"], "rules") == "rules"]
        live += [r for k in INDICES if config.SYSTEM_DEFAULTS.get(k) == "trend_rider"
                 for r in got[k].get(("tr", m, e, rd), [])]
        return sorted(live, key=lambda r: r["when"])

    tot = lambda rows: sum(r["net"] for r in rows)
    W = 186
    for setup, name in (("rules", "A. THE TOOL'S RULES on Nifty, Bank Nifty and Sensex"),
                        ("live", "B. LIVE TODAY: the Trend Rider on Nifty and Bank Nifty, the rules on Sensex")):
        print("=" * W)
        print(f" {name} - per lot, after costs.  MIDDLE reading: trades, won, total, profit factor, worst drop, worst "
              f"day by period; then the 3-year total if every both-way candle had been the stop / the target")
        print("=" * W)
        print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR{'':42s}| 3 YEARS: stop first / target first")
        res = {}
        for m in sc.MOMENTUM:
            print("-" * W)
            print(f" ENTRY: {sc.MLABEL[m]}")
            print(sds.line("   exit: today's (15-minute harness)", today[(setup, m)]))
            for e in EXITS:
                rows = {rd: rows_for(setup, m, e, rd) for rd in READ}
                res[(m, e)] = rows
                print(sds.line(f"   exit: {ELABEL[e]}", rows["mid"])
                      + f"  | ₹{tot(rows['cons']):>+10,.0f} / ₹{tot(rows['opt']):>+10,.0f}"
                      + f"   ({sum(r['amb'] for r in rows['mid'])} of {len(rows['mid'])} trades in a both-way candle)")
        print("-" * W)
        print("\n VERDICT (middle reading) vs today - at the signal with today's exit; KEEP only if more profit in BOTH periods")
        for m in sc.MOMENTUM:
            for e in EXITS:
                print(sds.verdict(f"{sc.MLABEL[m]}, {ELABEL[e]}", res[(m, e)]["mid"], today[(setup, "signal")]))
        print("\n BIG LOTS (middle reading) - the 3-year total / the held-out year, charges at the size; then with half a "
              "point more slippage a side")
        print(f"   {'':52s}" + "".join(f"{str(n) + ' lot' + ('s' if n > 1 else ''):>26s}" for n in sc.LOTS))
        for m in sc.MOMENTUM:
            for e in ("p11", "i11"):
                for extra in (0.0, sc.EXTRA_SLIP):
                    cells = []
                    for n_ in sc.LOTS:
                        s_ = sds.split(sc.scaled(res[(m, e)]["mid"], n_, extra if n_ > 1 else 0.0))
                        cells.append(f"₹{s_['is']['total'] + s_['oos']['total']:>+12,.0f} / {s_['oos']['total']:>+10,.0f}")
                    tag = f"{sc.MLABEL[m]}, {ELABEL[e]}" + (" +0.5 pt slip" if extra else "")
                    print(f"   {tag:52s}" + "".join(f"{x:>26s}" for x in cells))
        print()


if __name__ == "__main__":
    main()
