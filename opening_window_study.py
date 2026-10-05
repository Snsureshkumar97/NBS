#!/usr/bin/env python3
"""
opening_window_study.py — no new Indian-index entry in the first 15 / 30 minutes of the day
================================================================================
The user, 5 Oct 2026, after a losing day: all three tickets were calls bought 09:21-09:32 into the
opening pop (Bank Nifty one minute after its day high), then the market fell 1-1.5% to 12:08. "yes run
the first 15 and 30 minutes test".

HISTORY, BEFORE ANY RESULT: the tool had a version of this until 16 Sep 2026 - REGIME_OR_BREAK, wait out
09:15-09:45 and then need a break of that range - switched off at the user's choice for more trades
(open_and_rr_study.py: mixed, better in-sample, worse held-out). That was measured before five fixes to
the studies themselves: one open position per index, the real T1-ratchet, the deployed cooldown waiver,
the Supertrend trail and the 2-hour breakeven. And the 2 Oct studies (adx_gate_25 / rsi_cap / di_vote /
range_exhaustion) gated their baseline with that opening-range rule, which is OFF live. This study uses
the live entry checks exactly as configured (reversal_exit_study.live_gate -> tickets.py's own holds).

THE EXIT, AS LIVE (tickets.py _check_price, read 5 Oct 2026): the stop; T1 touched -> the stop moves to
T1's price; then it trails the index Supertrend; out at EXIT_AT_TARGET (T2); out at the day's close.
PLUS the 2-hour breakeven, priced the way it actually fills: 2 hours (8 candles) after entry with T1
untouched the stop moves to the entry - and if the price is ALREADY past it, the ticket is sold at the
market there and then (5 Oct: Bank Nifty sold ~430 points under its entry, -4,378). time_breakeven_
study.py filled every such exit AT the entry price - flagged as optimistic since 2 Oct; the line
"old breakeven fill" shows how much that flattered it. Not modelled (same for every row): the early exit
on a sustained reversal, which needs ticks.

ON 15-MINUTE CANDLES the earliest backtest entry is 09:30 (the close of the 09:15 candle); live enters
mid-candle (09:21 on 5 Oct). So "skip the first 15 minutes" = no entry off the 09:15 candle (earliest
09:45), "skip the first 30" = none off the 09:15 or 09:30 candles (earliest 10:00). 45 and 60 are run
only to see whether the pattern is smooth or one lucky line. A variant is KEPT only if it beats today
in BOTH periods (in-sample to 15 Aug 2025, then held-out).

    python3 opening_window_study.py
"""
import datetime as dt
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import conditional_cooldown_study as ccs
import config
import indicators as ind
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res

INDICES = rs.INDICES
SPLIT = rs.SPLIT
WAIT_BARS = 8          # TIME_BREAKEVEN_MINUTES = 120 on 15-minute candles


def simulate_live(A, i, tr, st, target, realistic_be=True, wait_bars=WAIT_BARS, hold_bars=26):
    """[(exit spot, bar, weight)] under the live exit. Stop before target inside a candle (the candle
    does not say which came first). realistic_be=False reproduces time_breakeven_study.py's fill."""
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    stop, t1_done, be_done = tr["stop"], False, False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if not realistic_be and not t1_done and (j - i) >= wait_bars:
            stop = max(stop, tr["entry"]) if ce else min(stop, tr["entry"])
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(stop, j, 1.0)]
        if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_done = True
            stop = max(stop, tr["t1"]) if ce else min(stop, tr["t1"])
        if t1_done and st[j] == st[j]:
            stop = max(stop, st[j]) if ce else min(stop, st[j])
        tgt = tr.get(target)
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            return [(tgt, j, 1.0)]
        if realistic_be and not t1_done and not be_done and (j - i) >= wait_bars:
            be_done = True
            if (cl[j] <= tr["entry"]) if ce else (cl[j] >= tr["entry"]):
                return [(cl[j], j, 1.0)]          # the stop moves to entry with price already past it: sold now
            stop = max(stop, tr["entry"]) if ce else min(stop, tr["entry"])
        if end[j]:
            return [(cl[j], j, 1.0)]
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)]


def window_gate(base, df, skip_minutes):
    """base AND 'the signal candle starts at or after 09:15 + skip_minutes'."""
    start = (dt.datetime(2000, 1, 1, 9, 15) + dt.timedelta(minutes=skip_minutes)).time()
    times = [t.time() for t in df.index]

    def gate(i, rec):
        return times[i] >= start and base(i, rec)
    return gate


def candidates(k, df, gate, A, F, pre, st, target, realistic_be, wait_bars=WAIT_BARS):
    out = bt.run(k, df, gate=gate)
    pos = {t: n for n, t in enumerate(df.index)}
    sig = F["sigma"].to_numpy()
    adx = pre["adx"]
    rows = []
    for tr in out["trades"]:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        legs = simulate_live(A, i, tr, st, target, realistic_be=realistic_be, wait_bars=wait_bars)
        p = ps.price(k, df, A, i, tr, legs, s)
        if p is None:
            continue
        exit_px, exit_bar, _ = legs[-1]
        p["side"] = tr["side"]
        p["closed_via"] = ("stop" if abs(exit_px - tr["stop"]) < 1e-6 else
                           "target" if tr.get(target) is not None and abs(exit_px - tr[target]) < 1e-6 else "other")
        p["exit_adx"] = float(adx.iloc[exit_bar])
        p["signal_time"] = df.index[i].time()
        p["index"] = k
        rows.append(p)
    rows.sort(key=lambda r: r["when"])
    return ccs.sequential_trades_conditional(rows, base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
                                             waive_adx_threshold=config.ADX_TREND_THRESHOLD)


def split(rows):
    return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]) or _empty(),
            "oos": ps.stats([r for r in rows if r["when"] >= SPLIT]) or _empty()}


def _empty():
    return {"n": 0, "total": 0.0, "pf": 0.0, "dd": 0.0, "win": 0.0, "avg": 0.0}


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A, pre, st = {}, {}, {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
        pre[k] = bt.precompute(df, k)
        st_len, st_mult = config.supertrend_params(k)
        st[k] = ind.supertrend(df, st_len, st_mult)[0].to_numpy()
    target = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    live = {k: (lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k])) for k in INDICES}

    def run(skip, realistic_be=True, wait_bars=WAIT_BARS):
        rows = []
        for k in INDICES:
            gate = live[k] if not skip else window_gate(live[k], hists[k], skip)
            rows += candidates(k, hists[k], gate, A[k], F[k], pre[k], st[k], target, realistic_be, wait_bars)
        return rows

    def line(name, rows):
        r = split(rows)
        a, b = r["is"], r["oos"]
        return (f" {name:44s} {a['n']:>5} {a['win']:>4.0f}% ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} {b['win']:>4.0f}% ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("=" * 150)
    print(" NIFTY + Bank Nifty + Sensex, per lot, real expiries, after costs. Entries: the live checks as configured "
          f"(REGIME_OR_BREAK={config.REGIME_OR_BREAK}).")
    print(f" Exit as live: stop, T1 -> stop to T1, Supertrend trail, out at {target.upper()}, 2-hour breakeven filled "
          "at the market when already past, day-end close.")
    print("=" * 150)
    print(f" {'':44s} {'IN-SAMPLE: trades, won, total, profit factor, worst drop':58s}  | HELD-OUT FINAL YEAR")
    print("-" * 150)
    base = run(0)
    print(line("today (entries from the first candle)", base))
    old = run(0, realistic_be=False)
    print(line("  same, old breakeven fill (at entry) - reference", old))
    # The 2-hour breakeven itself, now that it is priced as it fills: is it still better than none?
    no_be = run(0, wait_bars=10 ** 9)
    print(line("  same, WITHOUT the 2-hour breakeven", no_be))
    print("-" * 150)
    results = {}
    for skip, label in ((15, "skip the first 15 minutes (from 09:30 candle)"),
                        (30, "skip the first 30 minutes (from 09:45 candle)"),
                        (45, "  skip 45 (robustness only)"), (60, "  skip 60 (robustness only)")):
        results[skip] = run(skip)
        print(line(label, results[skip]))

    b = split(base)
    nb = split(no_be)
    print("\n THE 2-HOUR BREAKEVEN (live since 29 Sep), PRICED AS IT FILLS - better than none in both periods?")
    di, do = b["is"]["total"] - nb["is"]["total"], b["oos"]["total"] - nb["oos"]["total"]
    print(f"   {'still earns its place' if di > 0 and do > 0 else 'NO LONGER clears the bar'}:  with it vs without - "
          f"in-sample {di:>+10,.0f}  held-out {do:>+10,.0f}   worst drop: in-sample "
          f"{b['is']['dd'] - nb['is']['dd']:>+9,.0f}  held-out {b['oos']['dd'] - nb['oos']['dd']:>+9,.0f}")
    print("\n VERDICT - kept only if better than today in BOTH periods")
    for skip in (15, 30, 45, 60):
        r = split(results[skip])
        di, do = r["is"]["total"] - b["is"]["total"], r["oos"]["total"] - b["oos"]["total"]
        dd_i, dd_o = r["is"]["dd"] - b["is"]["dd"], r["oos"]["dd"] - b["oos"]["dd"]
        print(f"   {'KEEP' if di > 0 and do > 0 else 'drop'}  skip {skip:>2} min   in-sample {di:>+10,.0f}  held-out {do:>+10,.0f}"
              f"   worst drop: in-sample {dd_i:>+9,.0f}  held-out {dd_o:>+9,.0f}")

    print("\n TODAY'S OWN TRADES, BY THE CANDLE THEY WERE TAKEN OFF")
    print(f" {'signal candle':26s} {'IN-SAMPLE: n, won, total, per trade':44s}  | HELD-OUT")
    def bucket(label, pick):
        r = split([x for x in base if pick(x["signal_time"])])
        a, c = r["is"], r["oos"]
        print(f" {label:26s} {a['n']:>5} {a['win']:>4.0f}% ₹{a['total']:>+10,.0f} ₹{a['avg']:>+6,.0f}/trade"
              f"  | {c['n']:>4} {c['win']:>4.0f}% ₹{c['total']:>+10,.0f} ₹{c['avg']:>+6,.0f}/trade")
    bucket("09:15 (entry ~09:30)", lambda t: t == dt.time(9, 15))
    bucket("09:30 (entry ~09:45)", lambda t: t == dt.time(9, 30))
    bucket("09:45-10:45", lambda t: dt.time(9, 45) <= t <= dt.time(10, 45))
    bucket("11:00 onward", lambda t: t >= dt.time(11, 0))
    print("\n BY INDEX (in-sample / held-out totals)")
    for k in INDICES:
        cells = []
        for rows in (base, results[15], results[30]):
            r = split([x for x in rows if x["index"] == k])
            cells.append(f"₹{r['is']['total']:>+9,.0f} / ₹{r['oos']['total']:>+9,.0f}")
        print(f"   {k:10s} today {cells[0]}   skip 15 {cells[1]}   skip 30 {cells[2]}")
    return {"base": base, **results}


if __name__ == "__main__":
    main()
