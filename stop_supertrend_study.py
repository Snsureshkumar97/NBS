#!/usr/bin/env python3
"""
stop_supertrend_study.py — the Indian-index stop on the Supertrend line instead of the swing, on 3 years
================================================================================
The user, 7 Oct 2026: "what will be the result if we make stop loss at supertrend in indian market".

TODAY'S STOP (signal_engine.build_recommendation): beyond the last 12-candle swing low/high plus 0.15 ATR, an ATR
fallback when the swing is too close, capped at 2 ATR. After T1 the stop already follows the index Supertrend
(TRAIL_AFTER_T1_SUPERTREND, kept 2 Oct). The line is supertrend_params(): (10, 2.5) for Nifty and Sensex, (10, 3.0)
for Bank Nifty.

VERSIONS (config.SL_MODE sets the stop INSIDE the engine, so the room-to-run check, the ticket's reward:risk gate and
the premium stop all use it - a stop moved after the fact would let through trades the real gates refuse):
  1  stop ON the line          the line when it is below a call / above a put; else today's stop
  2  ...only those trades      same, but no trade at all when the line is on the wrong side
  3  the line only if tighter  the line only when it is closer than today's stop
  4  1 + follow the line       the stop moves up (down for puts) with the line from entry, not only after T1
  5  today's stop + follow     today's stop, following the line from entry

Everything else as stop_day_study.py / opening_window_study.py: live entry checks, live exit (T1 -> stop to T1,
Supertrend trail, out at T2, the 2-hour breakeven priced as it fills, day end), one position per index, the cooldown
waiver, per lot after costs, real expiries. KEEP only if more profit than today in BOTH periods.

    python3 stop_supertrend_study.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import config
import opening_window_study as ows
import pro_study as ps
import stop_day_study as sds

INDICES = sds.INDICES
SPECS = {
    "today": ("swing", False, False),
    "1 stop on the Supertrend line": ("supertrend", False, False),
    "2 ...only when the line is on the trade's side": ("supertrend", True, False),
    "3 the line only when tighter than today": ("tighter", False, False),
    "4 stop on the line, following it from entry": ("supertrend", False, True),
    "5 today's stop, following the line from entry": ("swing", False, True),
}


def simulate(A, i, tr, st, target, follow_from_entry):
    """ows.simulate_live, plus (follow_from_entry) the stop tightening to the Supertrend line from the first candle,
    not only after T1. The line is moved to only when it is on the trade's side of that candle's close (a line on
    the other side means the price has already crossed it), and like the post-T1 trail, a completed candle's line
    applies from the next candle."""
    if not follow_from_entry:
        return ows.simulate_live(A, i, tr, st, target)
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    stop, t1_done, be_done = tr["stop"], False, False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + 26, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(stop, j, 1.0)]
        if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_done = True
            stop = max(stop, tr["t1"]) if ce else min(stop, tr["t1"])
        if st[j] == st[j] and ((st[j] < cl[j]) if ce else (st[j] > cl[j])):
            stop = max(stop, st[j]) if ce else min(stop, st[j])
        tgt = tr.get(target)
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            return [(tgt, j, 1.0)]
        if not t1_done and not be_done and (j - i) >= ows.WAIT_BARS:
            be_done = True
            if (cl[j] <= tr["entry"]) if ce else (cl[j] >= tr["entry"]):
                return [(cl[j], j, 1.0)]
            stop = max(stop, tr["entry"]) if ce else min(stop, tr["entry"])
        if end[j]:
            return [(cl[j], j, 1.0)]
    last = min(i + 26, n - 1)
    return [(cl[last], last, 1.0)]


def _replay(name):
    mode, only_right_side, follow = SPECS[name]
    config.SL_MODE = mode                                  # set on every call: a worker runs several versions
    c = sds._context()
    rows, stops = [], []
    for k in INDICES:
        df, pre, st = c["hists"][k], c["pre"][k], c["st"][k]
        live = c["live"][k]
        if only_right_side:
            def gate(i, rec, live=live):
                line = rec.get("supertrend")
                ok = line is not None and ((line < rec["spot"]) if rec["option_type"] == "CE" else (line > rec["spot"]))
                return ok and live(i, rec)
        else:
            gate = live
        out = bt.run(k, df, gate=gate)
        pos = {t: n for n, t in enumerate(df.index)}
        sig = c["F"][k]["sigma"].to_numpy()
        atr = pre["atr"].to_numpy()
        adx = pre["adx"]
        for tr in out["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            legs = simulate(c["A"][k], i, tr, st, c["target"], follow)
            p = ps.price(k, df, c["A"][k], i, tr, legs, s)
            if p is None:
                continue
            exit_px, exit_bar, _ = legs[-1]
            p["side"] = tr["side"]
            p["closed_via"] = ("stop" if abs(exit_px - tr["stop"]) < 1e-6 else
                               "target" if tr.get(c["target"]) is not None and abs(exit_px - tr[c["target"]]) < 1e-6
                               else "other")
            p["exit_adx"] = float(adx.iloc[exit_bar])
            p["index"] = k
            p["stop_atr"] = abs(tr["entry"] - tr["stop"]) / max(float(atr[i]), 1e-9)
            line = float(st[i])
            p["line_right_side"] = line == line and ((line < tr["entry"]) if tr["side"] == "CE" else (line > tr["entry"]))
            rows.append(p)
    config.SL_MODE = "swing"
    return name, rows


def main():
    from concurrent.futures import ProcessPoolExecutor
    print(f"Replaying the live entries under {len(SPECS)} stop rules, side by side...", flush=True)
    with ProcessPoolExecutor(max_workers=len(SPECS)) as ex:
        got = dict(ex.map(_replay, list(SPECS)))
    res = {name: sds.per_index(rows) for name, rows in got.items()}
    base = res["today"]

    print("=" * 172)
    print(" NIFTY + Bank Nifty + Sensex, per lot, real expiries, after costs. Live entry checks, live exit (T1 -> stop to "
          "T1, Supertrend trail, out at T2, 2-hour breakeven priced as it fills), one position per index, cooldown waiver.")
    print(" columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print("-" * 172)
    for name, rows in res.items():
        print(sds.line(name, rows))

    print("\n VERDICT - KEEP only if more profit than today in BOTH periods (worst drop / worst day: minus = better)")
    for name, rows in res.items():
        if name != "today":
            print(sds.verdict(name, rows, base))

    print("\n THE STOPS THEMSELVES (all periods): how far from the entry, how often hit")
    for name, rows in res.items():
        d = np.array([r["stop_atr"] for r in rows])
        hit = np.mean([r["closed_via"] == "stop" for r in rows]) * 100
        print(f"   {name:48s} stop {np.median(d):4.2f} ATR away (median), {np.mean(d):4.2f} (mean);  closed at the stop "
              f"{hit:4.1f}% of trades")
    b = res["today"]
    right = [r for r in b if r["line_right_side"]]
    wrong = [r for r in b if not r["line_right_side"]]
    print(f"\n TODAY'S TRADES BY WHERE THE LINE WAS AT ENTRY: on the trade's side {len(right)} of {len(b)} "
          f"({100 * len(right) / max(len(b), 1):.0f}%), on the wrong side {len(wrong)}")
    for label, sel in (("line on the trade's side", right), ("line on the wrong side", wrong)):
        s = sds.split(sel)
        print(f"   {label:30s} in-sample {s['is']['n']:>4} trades ₹{s['is']['total']:>+10,.0f}"
              f" (₹{s['is']['avg'] if s['is']['n'] else 0:>+5,.0f}/trade)   held-out {s['oos']['n']:>4} trades "
              f"₹{s['oos']['total']:>+10,.0f} (₹{s['oos']['avg'] if s['oos']['n'] else 0:>+5,.0f}/trade)")


if __name__ == "__main__":
    main()
