#!/usr/bin/env python3
"""
stop_target_sweep.py — how far the Indian-index stop may sit, and which target to exit at, on 3 years
================================================================================
The user, 7 Oct 2026: "so what will be the best stop loss entry. and targets you would suggest".

THE TWO DIALS, AS LIVE TODAY:
  stop    beyond the last 12-candle swing (+0.15 ATR), CAPPED at MAX_RISK_ATR_MULT = 2.0 ATR - and the cap is what
          sets most stops: stop_supertrend_study.py found today's median stop exactly 2.00 ATR from the entry. The cap
          also feeds the room-to-run check (reach / stop), so it decides which trades are taken at all.
  target  T1 / T2 / T3 = 40% / 70% / 100% of how far the market can reach (REACH_FRACTIONS); out at EXIT_AT_TARGET
          = T2, last measured 11 Sep 2026 (rule_review.py) - before the one-position-per-index walk, the real T1
          ratchet, the Supertrend trail and the 2-hour breakeven priced as it fills.

THE GRID: the cap at 1.0 / 1.5 / 2.0 / 2.5 / 3.0 ATR (set inside the engine, so the room-to-run check and the ticket's
reward:risk gate see it) x out at T1 / T2 / T3, everything else exactly as stop_day_study.py. The 2.0 / T2 cell IS
today and must reproduce stop_day_study's numbers (checked). Best-of-15 is a selection, so a cell is worth acting on
only if it beats today in BOTH periods AND its neighbours agree (a smooth pattern, not one lucky cell).

    python3 stop_target_sweep.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import config
import opening_window_study as ows
import pro_study as ps
import stop_day_study as sds

INDICES = sds.INDICES
CAPS = (1.0, 1.5, 2.0, 2.5, 3.0)
TARGETS = ("t1", "t2", "t3")


def _replay(cap):
    """{target: raw candidates} for one stop cap."""
    config.MAX_RISK_ATR_MULT = cap
    c = sds._context()
    out = {t: [] for t in TARGETS}
    for k in INDICES:
        df, pre, st = c["hists"][k], c["pre"][k], c["st"][k]
        trades = bt.run(k, df, gate=c["live"][k])["trades"]
        pos = {t: n for n, t in enumerate(df.index)}
        sig = c["F"][k]["sigma"].to_numpy()
        adx = pre["adx"]
        for tr in trades:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            for target in TARGETS:
                legs = ows.simulate_live(c["A"][k], i, tr, st, target)
                p = ps.price(k, df, c["A"][k], i, tr, legs, s)
                if p is None:
                    continue
                exit_px, exit_bar, _ = legs[-1]
                p["side"] = tr["side"]
                p["closed_via"] = ("stop" if abs(exit_px - tr["stop"]) < 1e-6 else
                                   "target" if tr.get(target) is not None and abs(exit_px - tr[target]) < 1e-6 else "other")
                p["exit_adx"] = float(adx.iloc[exit_bar])
                p["index"] = k
                out[target].append(p)
    config.MAX_RISK_ATR_MULT = 2.0
    return cap, out


def main():
    from concurrent.futures import ProcessPoolExecutor
    print(f"Replaying the live entries under {len(CAPS)} stop caps x {len(TARGETS)} exit targets...", flush=True)
    with ProcessPoolExecutor(max_workers=len(CAPS)) as ex:
        got = dict(ex.map(_replay, CAPS))
    res = {(cap, t): sds.per_index(got[cap][t]) for cap in CAPS for t in TARGETS}
    base = res[(2.0, "t2")]
    b = sds.split(base)
    assert round(b["is"]["total"]) == 437509 and round(b["oos"]["total"]) == 125227, \
        f"the 2.0 / T2 cell must be today (stop_day_study): {b['is']['total']:.0f} / {b['oos']['total']:.0f}"

    print("=" * 172)
    print(" NIFTY + Bank Nifty + Sensex, per lot, real expiries, after costs. Live entry checks, live exit (T1 -> stop to "
          "T1, Supertrend trail, 2-hour breakeven priced as it fills), one position per index, cooldown waiver.")
    print(" columns: trades, won, total, profit factor, worst drop, worst single DAY.   TODAY = stop cap 2.0 ATR, out at T2")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for cap in CAPS:
        print("-" * 172)
        for t in TARGETS:
            name = f"stop cap {cap:.1f} ATR, out at {t.upper()}" + ("   <- TODAY" if (cap, t) == (2.0, "t2") else "")
            print(sds.line(name, res[(cap, t)]))

    print("\n GRID - change in total profit vs today (in-sample / held-out), Rs per lot; * = better in BOTH periods")
    print(f"   {'':14s}" + "".join(f"{'out at ' + t.upper():>30s}" for t in TARGETS))
    for cap in CAPS:
        cells = []
        for t in TARGETS:
            r = sds.split(res[(cap, t)])
            di, do = r["is"]["total"] - b["is"]["total"], r["oos"]["total"] - b["oos"]["total"]
            star = "*" if di > 0 and do > 0 else (" " if (cap, t) != (2.0, "t2") else "=")
            cells.append(f"{di:>+12,.0f} /{do:>+10,.0f} {star}")
        print(f"   cap {cap:.1f} ATR    " + "".join(f"{c:>30s}" for c in cells))

    print("\n VERDICT - KEEP only if more profit than today in BOTH periods (worst drop / worst day: minus = better)")
    for cap in CAPS:
        for t in TARGETS:
            if (cap, t) != (2.0, "t2"):
                print(sds.verdict(f"stop cap {cap:.1f} ATR, out at {t.upper()}", res[(cap, t)], base))


if __name__ == "__main__":
    main()
