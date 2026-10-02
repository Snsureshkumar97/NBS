#!/usr/bin/env python3
"""
ladder_confirm_study.py — the user's own ladder idea, hand-described, 2 Oct 2026
================================================================================
Said by hand, cleaned up here without changing the mechanic: take at least 4
lots. The first lot books the instant T1 is touched, exactly like every other
partial-exit policy in pro_study.py. For whatever remains, the stop does NOT
move to breakeven on that touch - it stays exactly where it was until a
CONFIRMING candle CLOSES beyond T1 (not merely touches it, because a bare
touch is routinely retested - the user's own words, "if it comes back and
hit t1 as usual" - and a stop sitting right at T1 would be taken out by an
ordinary retest, not a real reversal). Once that candle closes beyond T1,
the stop ratchets to THAT CANDLE'S OWN LOW - not to T1 itself, which is the
whole point of waiting for the close: it buys the retest some room. The same
pair of steps (touch -> book one lot; later, a confirming close -> stop to
that candle's low) repeats at T2, then T3.

"AND IT GOES ON" PAST T3 - an interpretation, not a transcript
    The user's description names no T4. Implemented here as the identical
    rule with no further fixed target: once all three lots are gone, whenever
    a candle's close marks a fresh extension beyond the best close reached
    since the last ratchet, the stop moves to THAT candle's own low/high -
    indefinitely, the same "close confirms, that candle's own extreme becomes
    the new stop" rule, just unbounded. This is a judgement call about intent
    behind an instruction that stops naming targets after T3, and should be
    checked against what the user actually meant before trusting the result.

GRANULARITY - APPROXIMATED ON 15-MINUTE CANDLES, NOT TRUE 5-MINUTE
    The user described this in terms of 5-minute closes. Real 5-minute
    history for 3 years was not available this session - the local login
    flow couldn't reach the registered Redirect URL (pinned to the
    production VM, correctly, for live trading), and reading the VM's own
    per-user stored token to work around that was itself blocked by this
    environment's credential-access protection. Offered the choice, the user
    picked running on 15-minute candles now over chasing a fresh Kite app or
    a manual token. THIS IS A REAL, MATERIAL SUBSTITUTION, not a minor detail:
    confirming on a 15-minute close is three times slower than a 5-minute one
    on average, and a 15-minute candle's own low (where the stop lands) sits
    further from the breakout than a 5-minute candle's would - both make this
    run a slower, looser version of what the user actually described. The
    result below should be read as "does a candle-confirmed ladder of this
    shape help at all", not a faithful test of the exact 5-minute idea.
    simulate_ladder() itself is timeframe-agnostic (bar_minutes is a
    parameter, nothing is hard-coded) - a true 5-minute run is a straight
    re-run against finer data later, not a rewrite, if that access is ever
    sorted out.

    Because this now runs on the SAME 15-minute bars pro_study.py's entries
    and pro_study.price() already use, there is no separate data pull and no
    separate pricer needed - simulate_ladder() takes pro_study.py's own A[k]
    dict (with "bar_minutes": 15 added) and legs price straight through
    pro_study.price() unmodified, bar-index-aligned with df exactly as every
    other exit variant in that file already is.
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


# ================================================================== the exit
def simulate_ladder(C, idx, tr, n_lots=4, hold_bars=26, square_off=True):
    """Confirmed-candle ladder exit. See this file's own docstring for the
    full mechanic and the granularity caveat. Returns legs like
    pro_study.simulate(): [(exit_spot, bar, weight), ...] - bar is a position
    in C, priced exactly like any other exit variant when C is pro_study's
    own A[k] (same df, same bar width).

    C: hi/lo/cl/end dict, whatever timeframe was actually passed in.
    idx: integer position in C of the entry bar.
    tr: same trade dict shape as pro_study.simulate() (side/entry/stop/t1/t2/t3).
    n_lots: total lots the position opens with (>= 3; one exits at each of
    T1/T2/T3, anything beyond that is the runner lot(s) under "it goes on").
    """
    if n_lots < 3:
        raise ValueError("need at least one lot for each of T1/T2/T3")
    hi, lo, cl, end = C["hi"], C["lo"], C["cl"], C["end"]
    n = len(cl)
    ce = tr["side"] == "CE"
    targets = [tr["t1"], tr["t2"], tr["t3"]]
    lot_w = 1.0 / n_lots
    stop = tr["stop"]
    rem = 1.0
    touched = 0        # rungs (T1/T2/T3) whose lot has been booked
    confirmed = 0       # rungs whose ratchet has been confirmed
    best = None         # best confirmed close since T3, once all 3 are gone
    legs = []

    for j in range(idx + 1, min(idx + 1 + hold_bars, n)):
        # 1. stop first inside the bar - same pessimistic convention as
        #    pro_study.simulate(): the bar does not say which came first.
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            legs.append((stop, j, rem))
            return legs
        # 2. a touch of the next un-booked rung books one lot, instantly -
        #    "reach" in the user's own words, not "close".
        if touched < 3:
            lvl = targets[touched]
            if (hi[j] >= lvl) if ce else (lo[j] <= lvl):
                legs.append((lvl, j, lot_w))
                rem -= lot_w
                touched += 1
        # 3. confirmation for the earliest touched-but-unconfirmed rung: a
        #    CLOSE beyond it (not a touch) ratchets the stop to THIS candle's
        #    own low/high. One confirmation per bar, in order - a single
        #    runaway candle that clears more than one rung at once still
        #    confirms them one bar at a time, not all in the same tick.
        if confirmed < touched:
            lvl = targets[confirmed]
            if (cl[j] > lvl) if ce else (cl[j] < lvl):
                stop = lo[j] if ce else hi[j]
                confirmed += 1
                if confirmed == 3:
                    best = cl[j]
        # 4. past T3, the identical rule with no further named target: a
        #    fresh confirmed extension ratchets the stop to that candle's own
        #    low/high - "and it goes on".
        elif confirmed == 3:
            if (cl[j] > best) if ce else (cl[j] < best):
                best = cl[j]
                stop = lo[j] if ce else hi[j]
        if square_off and end[j]:
            legs.append((cl[j], j, rem))
            return legs
    last = min(idx + 1 + hold_bars, n) - 1
    legs.append((cl[last], last, rem))
    return legs


# ================================================================== the study
def main():
    print("Loading history (15-minute candles - see this file's own docstring "
          "for why this is an approximation of the user's 5-minute idea, not "
          "a faithful run of it)...")
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

    def evaluate(exit_fn):
        rows = []
        for k in INDICES:
            df = hists[k]
            res = bt.run(k, df, gate=base_gate[k])
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in res["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = exit_fn(A[k], i, tr)
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return ({"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                 "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}, rows)

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:34s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("=" * 128)
    print(" All per lot, pooled across NIFTY / BANKNIFTY / SENSEX, real expiries, after costs")
    print(" GRANULARITY: 15-minute candles approximating the user's 5-minute ladder idea - see docstring")
    print("=" * 128)
    hdr = (f" {'variant':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print(hdr); print("-" * 128)

    base = evaluate(lambda A_, i, tr: ps.simulate(A_, i, tr))[0]
    print(line("LIVE RULES (OR break, hold to T3/stop)", base))

    out = {"LIVE RULES (OR break, hold to T3/stop)": base}
    for n_lots in (4,):
        name = f"confirmed-candle ladder ({n_lots} lots)"
        r = evaluate(lambda A_, i, tr, n_lots=n_lots: simulate_ladder(A_, i, tr, n_lots=n_lots))[0]
        out[name] = r
        print(line(name, r))

    print("\n VERDICT — kept only if better than LIVE RULES both in-sample and out-of-sample")
    b = out["LIVE RULES (OR break, hold to T3/stop)"]
    for name, r in out.items():
        if name.startswith("LIVE"):
            continue
        di = r["is"]["total"] - b["is"]["total"]; do = r["oos"]["total"] - b["oos"]["total"]
        dd = r["oos"]["dd"] - b["oos"]["dd"]
        keep = di > 0 and do > 0
        print(f"   {'KEEP ' if keep else 'drop '} {name:34s} in-sample {di:>+10,.0f}   out-of-sample {do:>+10,.0f}"
              f"   held-out drawdown {dd:>+9,.0f}")
    return out


if __name__ == "__main__":
    main()
