#!/usr/bin/env python3
"""
t1_ratchet_delay_study.py — should the stop wait before moving to T1?
========================================================================================
The user, 28 Sep 2026: "after crossing t1 dont make t1 stop loss as soon it cross you can
give me suggestion how long after crossing t1 we should make t1 as stop loss."

TODAY'S LIVE BEHAVIOR (tickets.py's _check_price, see its own docstring - built 22 Sep 2026):
each target strictly before the trade's exit rung becomes the new stop THE INSTANT price
crosses it. With today's config.EXIT_AT_TARGET = "T2", the only target that ratchets is T1
(there is nothing before T1 to ratchet from, and T2 itself is the exit, not a waypoint). So
this is specifically the T1-to-stop ratchet, exactly as the user named it.

NEITHER pro_study.py's simulate() NOR the earlier target_ladder_study.py model this ratchet at
all - simulate()'s only stop-tightening option is be_after_t1 (breakeven at ENTRY, not at T1's
own price), which is a different thing entirely. So this file carries its own bar-level
simulator, built to reproduce today's instant ratchet exactly at delay_bars=0 (the baseline
every candidate delay below is measured against), before adding a delay on top of it.

THE MECHANISM, per the user's own choice when asked how a pullback during the wait should be
handled ("only lock in T1 if price is still above it when the wait elapses"):
    delay_bars = 0   today, unchanged: the stop moves to T1 on the very bar price touches it.
    delay_bars > 0   the stop does NOT move on the touch. Starting delay_bars bars later, each
                     bar's CLOSE is checked (not a wick - "still above it" reads as where price
                     settled, not a momentary poke back through T1); the stop moves to T1 the
                     first bar the close still qualifies. If price is back through T1 exactly
                     when the wait is up, nothing happens THAT bar - but it keeps checking every
                     bar after, and ratchets on the first one that does qualify. It can never
                     un-ratchet once it has (same invariant tickets.py's own stop has: it only
                     ever moves in the trade's favour).
    The original stop is checked FIRST every bar, before either the touch or the ratchet - the
    same pessimistic ordering pro_study.py and tickets.py both already use, since a single 15-
    minute bar cannot say which of two levels it crossed first.

ENTRIES: identical to target_ladder_study.py and reversal_exit_study.py - today's real gates
(tickets.py's _regime_hold / _divergence_hold / _reward_hold / _spread_hold, called directly),
so every delay below is measured on the exact same set of trades; only the ratchet timing
differs between rows.

PRICING: pro_study.py's own - real expiries, Black-Scholes off VIX-scaled realised volatility,
Zerodha's real costs, 0.25% slippage a side.

LIMITS, same family as reversal_exit_study.py's: delay_bars is expressed in whole 15-minute
bars because that is this backtest's only resolution - live, the wait would run on the
signal engine's own clock (like SIGNAL_CONFIRM_SECONDS elsewhere in tickets.py) and could be
tuned inside a single bar. A bar's close is used to mean "price now" at the check point,
which is one reasonable reading of "still above it" but not the only one a wick-based check
would give a different, noisier answer.

    python3 t1_ratchet_delay_study.py
"""
import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res

INDICES = ps.INDICES
SPLIT = ps.SPLIT


def simulate_delayed_ratchet(A, i, tr, delay_bars, hold_bars=26, square_off=True, target=None):
    """One trade's legs under a T1-ratchet delay of `delay_bars` 15-minute bars. See the module
    docstring for the exact mechanism and why delay_bars=0 reproduces today's live tool."""
    target = str(target or getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    if target not in ("t1", "t2", "t3"):
        target = "t2"
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    stop, legs, rem = tr["stop"], [], 1.0
    t1 = tr["t1"]
    ratcheted, touch_bar = (target == "t1"), None      # if T1 IS the exit there is nothing to ratchet from
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            legs.append((stop, j, rem)); return legs
        if not ratcheted and t1 is not None:
            touched_now = (hi[j] >= t1) if ce else (lo[j] <= t1)
            if touch_bar is None and touched_now:
                touch_bar = j
            if touch_bar is not None:
                if delay_bars <= 0:
                    stop, ratcheted = t1, True
                elif (j - touch_bar) >= delay_bars:
                    still_favorable = (cl[j] >= t1) if ce else (cl[j] <= t1)
                    if still_favorable:
                        stop, ratcheted = t1, True
        tgt = tr[target]
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            legs.append((tgt, j, rem)); return legs
        if square_off and end[j]:
            legs.append((cl[j], j, rem)); return legs
    last = min(i + hold_bars, n - 1)
    legs.append((cl[last], last, rem))
    return legs


def line(name, a, label_w=28):
    return (f" {name:{label_w}s} {a['n']:>5} ₹{a['total']:>+11,.0f}  "
            f"avg ₹{a['avg']:>+7,.0f}  win {a['win']:4.1f}%  PF {a['pf']:4.2f}  DD ₹{a['dd']:>9,.0f}")


def main():
    print("Loading 3 years of history (shared cache with pro_study.py)...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    import math
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                 "cl": df["Close"].to_numpy(),
                 "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                 "days": set(df.index.date)}
    gate = {k: (lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k])) for k in INDICES}

    print("Replaying today's real entry gates over 3 years...")
    trades_by_index = {}
    for k in INDICES:
        r = bt.run(k, hists[k], gate=gate[k])
        trades_by_index[k] = r["trades"]
        print(f"  {k}: {len(r['trades']):,} entries")

    def run_delay(delay_bars):
        rows = []
        for k in INDICES:
            df = hists[k]
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in trades_by_index[k]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = simulate_delayed_ratchet(A[k], i, tr, delay_bars)
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return rows

    # 0 = today (instant). Then 15-min steps out to 3 hours, plus "never" (no ratchet at all -
    # the T1-hit stays a waypoint note only, stop never leaves its original level) as the other
    # extreme, so the table shows the full range between the two current alternatives.
    CANDIDATES = [("0 (today, instant)", 0), ("15 min (1 bar)", 1), ("30 min (2 bars)", 2),
                  ("45 min (3 bars)", 3), ("1 hour (4 bars)", 4), ("1.5 hours (6 bars)", 6),
                  ("2 hours (8 bars)", 8), ("3 hours (12 bars)", 12), ("never (no ratchet)", 10 ** 9)]

    print("Pricing each delay as the real option, after real costs...")
    by_delay = {label: run_delay(bars) for label, bars in CANDIDATES}

    print("=" * 100)
    print(" Entries: today's real gates, unchanged across every row - only the T1-to-stop ratchet's")
    print(" delay differs. Exit rung: T2 (config.EXIT_AT_TARGET) - the only target that ratchets today.")
    print(" Mechanism: stop moves to T1 the first bar AT OR AFTER the delay whose CLOSE is still past")
    print(" T1 - never on a bar where price has slipped back through it. See this file's own docstring.")
    print("=" * 100)

    print("\nFULL 3 YEARS, POOLED ACROSS NIFTY / BANKNIFTY / SENSEX (per lot, after costs)")
    print("-" * 100)
    for label, _ in CANDIDATES:
        print(line(label, ps.stats(by_delay[label])))

    print("\nSPLIT BY PERIOD (in-sample to " + str(SPLIT.date()) + " | held-out final year after)")
    print("-" * 100)
    for label, _ in CANDIDATES:
        rows = by_delay[label]
        a = ps.stats([r for r in rows if r["when"] < SPLIT])
        b = ps.stats([r for r in rows if r["when"] >= SPLIT])
        print(f" {label:28s} "
              f"{a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
              f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("\nHOW OFTEN EACH DELAY ACTUALLY RATCHETS (vs. today's every-touch-ratchets)")
    print("-" * 100)
    base_rows = by_delay["0 (today, instant)"]
    base_by_key = {(r["index"], r["when"]): r for r in base_rows}
    for label, bars in CANDIDATES[1:]:
        rows = by_delay[label]
        # A ratchet happened iff the trade's realised exit differs from what today's instant
        # ratchet would have produced on the identical entry - a cheap proxy readable straight
        # off the priced legs without re-deriving the internal `ratcheted` flag.
        changed = sum(1 for r in rows if (r["index"], r["when"]) in base_by_key
                      and abs(r["net"] - base_by_key[(r["index"], r["when"])]["net"]) > 0.5)
        print(f" {label:28s} {changed:5d} of {len(rows):,} trades priced differently from today's instant ratchet")

    print("\nSame limits as pro_study.py throughout: constant IV across a trade, today's lot sizes")
    print("applied to the whole 3 years, no daily loss brake. delay_bars is whole 15-minute bars -")
    print("live would run on a finer clock, the same way SIGNAL_CONFIRM_SECONDS already does elsewhere.")
    return by_delay


if __name__ == "__main__":
    main()
