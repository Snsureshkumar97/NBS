#!/usr/bin/env python3
"""
target_second_trade_study.py — a tighter exit target, and a higher reward:risk for the day's later trades (3 years)
================================================================================
The user, 7 Oct 2026: "will it be good if we make the targets little bit more tighter or for the second trade let the
risk reward be little bit up to enter".

A. TIGHTER TARGET. Today the ticket exits at T2 = 70% of the room to run (REACH_FRACTIONS [0.4, 0.7, 1.0]); T1 (40%)
   moves the stop up to T1 and the Supertrend trail follows. Swept: out at 40 / 50 / 60 / 70 (today) / 85 / 100% of the
   room, everything else as live - T1's step-up stays at 40%.
B. A HIGHER BAR FOR THE NEXT TRADE. Today every entry needs reward:risk (T3 / stop distance - tickets.reward_risk_t3,
   T3 being the room to run) of at least 1.0.
   Raised to 1.25 / 1.5 / 2.0 only for a trade that is not the day's first:
     per index    - the second (and later) trade of the day on that index
     any index    - any trade after the day's first trade on any index
     after a loss - only once that index has closed a losing trade today
   The first trade of the day keeps today's bar. Applied inside the walk (one position per index, the cooldown and its
   waiver), so a refused trade frees its index for a later one exactly as live.

Everything else as stop_day_study.py: live entry checks, live exit (T1 -> stop to T1, Supertrend trail, the 2-hour
breakeven priced as it fills, the day's close), per lot after costs, real expiries. The 70% / no-rule cells must
reproduce today's numbers (checked). KEEP only if more profit than today in BOTH periods, with neighbours agreeing.

    python3 target_second_trade_study.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import config
import opening_window_study as ows
import pro_study as ps
import stop_day_study as sds

INDICES = sds.INDICES
FRACTIONS = (0.4, 0.5, 0.6, 0.7, 0.85, 1.0)
BARS = (1.25, 1.5, 2.0)


def _replay(k):
    """{fraction: [priced rows]} for one index's live entries, each row carrying its reward:risk."""
    c = sds._context()
    df, pre, st = c["hists"][k], c["pre"][k], c["st"][k]
    trades = bt.run(k, df, gate=c["live"][k])["trades"]
    pos = {t: n for n, t in enumerate(df.index)}
    sig = c["F"][k]["sigma"].to_numpy()
    adx = pre["adx"]
    out = {f: [] for f in FRACTIONS}
    for tr in trades:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        # The trade's own T2 scaled: T2 is 70% of the room to run, so f/0.7 of the way to it is f of the room - and on
        # the few trades with no room figure (no reach that day: T1/T2/T3 at 1/2/3 x the stop) the same share of
        # their own T2, so the 70% line is today's exit on EVERY trade (checked in main).
        for f in FRACTIONS:
            t = dict(tr, tx=round(tr["entry"] + (tr["t2"] - tr["entry"]) * f / 0.7, 2))
            legs = ows.simulate_live(c["A"][k], i, t, st, "tx")
            p = ps.price(k, df, c["A"][k], i, t, legs, s)
            if p is None:
                continue
            exit_px, exit_bar, _ = legs[-1]
            p["side"] = tr["side"]
            p["closed_via"] = ("stop" if abs(exit_px - tr["stop"]) < 1e-6 else
                               "target" if abs(exit_px - t["tx"]) < 1e-6 else "other")
            p["exit_adx"] = float(adx.iloc[exit_bar])
            p["index"] = k
            p["rr"] = abs(tr["t3"] - tr["entry"]) / max(tr["risk"], 1e-9)     # what the live gate reads: T3 / stop
            out[f].append(p)
    return k, out


def walk(raw, bar=None, scope=None):
    """The live walk (one position per index, the cooldown and its waiver) over all three indices in time order, plus:
    a trade that is not the day's first (scope "index" / "any" / "loss") needs reward:risk >= bar. bar None = today."""
    state = {k: {"open_until": None, "last": {"CE": None, "PE": None}} for k in INDICES}
    kept = []
    for tr in sorted(raw, key=lambda r: (r["when"], INDICES.index(r["index"]))):
        s = state[tr["index"]]
        if s["open_until"] is not None and tr["when"] < s["open_until"]:
            continue
        prior = s["last"][tr["side"]]
        if prior is not None:
            waived = prior["closed_via"] == "target" and prior["exit_adx"] >= config.ADX_TREND_THRESHOLD
            if tr["when"] < prior["close"] + pd.Timedelta(minutes=0 if waived else config.REENTRY_COOLDOWN_MIN):
                continue
        if bar is not None:
            day = tr["when"].date()
            today = [o for o in kept if o["when"].date() == day]
            later = {"index": any(o["index"] == tr["index"] for o in today),
                     "any": bool(today),
                     "loss": any(o["index"] == tr["index"] and o["exit_time"] <= tr["when"] and o["net"] < 0 for o in today)}[scope]
            if later and tr["rr"] < bar:
                continue
        kept.append(tr)
        s["open_until"] = tr["exit_time"]
        s["last"][tr["side"]] = {"close": tr["exit_time"], "closed_via": tr["closed_via"], "exit_adx": tr["exit_adx"]}
    return kept


def main():
    from concurrent.futures import ProcessPoolExecutor
    print("Replaying the live entries on all three indices, side by side...", flush=True)
    with ProcessPoolExecutor(max_workers=len(INDICES)) as ex:
        got = dict(ex.map(_replay, INDICES))
    raw = {f: [r for k in INDICES for r in got[k][f]] for f in FRACTIONS}
    base = sds.per_index(raw[0.7])
    b = sds.split(base)
    assert round(b["is"]["total"]) == 437509 and round(b["oos"]["total"]) == 125227, \
        f"out at 70% must be today: {b['is']['total']:.0f} / {b['oos']['total']:.0f}"
    assert [(r["index"], r["when"]) for r in walk(raw[0.7])] == [(r["index"], r["when"]) for r in base], \
        "walk() with no rule must be today's per-index walk"

    print("=" * 172)
    print(" NIFTY + Bank Nifty + Sensex, per lot, real expiries, after costs. Live entry checks, live exit, one position per "
          "index, cooldown waiver.  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print("-" * 172)
    print(" A. THE EXIT TARGET, as a share of the room to run (today: T2 = 70%)")
    res_a = {}
    for f in FRACTIONS:
        res_a[f] = sds.per_index(raw[f])
        print(sds.line(f"   out at {int(round(100 * f))}% of the room" + ("   <- TODAY" if f == 0.7 else ""), res_a[f]))
    print("-" * 172)
    print(" B. A HIGHER REWARD:RISK FOR A TRADE THAT IS NOT THE DAY'S FIRST (today: 1.0 for every trade)")
    res_b = {}
    for scope, label in (("index", "2nd+ trade on that index"), ("any", "any trade after the day's first"),
                         ("loss", "after a loss on that index")):
        for bar in BARS:
            res_b[(scope, bar)] = walk(raw[0.7], bar, scope)
            print(sds.line(f"   {label}, needs {bar:.2f}", res_b[(scope, bar)]), flush=True)

    print("\n VERDICT - KEEP only if more profit than today in BOTH periods (worst drop / worst day: minus = better)")
    for f in FRACTIONS:
        if f != 0.7:
            print(sds.verdict(f"out at {int(round(100 * f))}% of the room", res_a[f], base))
    for (scope, bar), rows in res_b.items():
        print(sds.verdict(f"{scope}: later trades need {bar:.2f}", rows, base))

    # How many trades are a day's later trades at all, and how they did under today's rule
    first, later = [], []
    seen = set()
    for r in base:
        key = (r["when"].date(), r["index"])
        (later if key in seen else first).append(r)
        seen.add(key)
    print("\n TODAY'S TRADES: THE DAY'S FIRST ON AN INDEX vs ITS LATER ONES (today's rules)")
    for label, sel in (("first trade of the day on the index", first), ("2nd+ trade of the day on the index", later)):
        s = sds.split(sel)
        print(f"   {label:38s} in-sample {s['is']['n']:>4} trades ₹{s['is']['total']:>+10,.0f} (₹{s['is']['avg'] if s['is']['n'] else 0:>+5,.0f}/trade)"
              f"   held-out {s['oos']['n']:>4} trades ₹{s['oos']['total']:>+10,.0f} (₹{s['oos']['avg'] if s['oos']['n'] else 0:>+5,.0f}/trade)")


if __name__ == "__main__":
    main()
