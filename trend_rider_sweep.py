#!/usr/bin/env python3
"""
trend_rider_sweep.py — is the pasted "Structural Trend Rider" (15-min, ADX 20, 1:2.5) a plateau or one lucky cell?
================================================================================
pasted_combos_study.py (7 Oct 2026) found ONE of its 8 versions beating the tool's live system in both periods: 15-minute
candles, ADX > 20 and rising, stop at the last 5 candles' low/high, target 2.5 x the risk - +544,405 / +142,079 per lot
against +437,509 / +125,227. Its neighbours wobble (1:2 loses held-out; ADX 25 collapses held-out). This sweeps around
it - ADX 18 / 20 / 22, the stop on the last 3 / 4 / 5 / 6 candles, target 2.0 / 2.25 / 2.5 / 2.75 / 3.0 x the risk -
and counts how many settings beat today in BOTH periods. A real edge shows as a block of them; noise as a scatter.

    python3 trend_rider_sweep.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pasted_combos_study as pc
import stop_day_study as sds

ADX = (18.0, 20.0, 22.0)
SWING = (3, 4, 5, 6)
RR = (2.0, 2.25, 2.5, 2.75, 3.0)
TODAY = (437509, 125227)


def _job(k):
    c = sds._context()
    df15 = c["hists"][k][["Open", "High", "Low", "Close", "Volume"]]
    days = set(df15.index.date)
    sig = c["F"][k]["sigma"]
    sigma_by_day = sig.groupby(sig.index.date).first().to_dict()
    return k, {(a, w, r): pc.run(k, df15, sigma_by_day, days, 2, 15, a, r, w) for a in ADX for w in SWING for r in RR}


def main():
    from concurrent.futures import ProcessPoolExecutor
    print("Sweeping the Structural Trend Rider on 15-minute candles, all three indices...", flush=True)
    with ProcessPoolExecutor(max_workers=3) as ex:
        got = dict(ex.map(_job, pc.INDICES))
    wins = 0
    for a in ADX:
        print(f"\n ADX > {a:g} and rising - change vs today per lot (in-sample / held-out); * = better in BOTH periods")
        print(f"   {'stop on last':14s}" + "".join(f"{'target ' + str(r) + 'R':>26s}" for r in RR))
        for w in SWING:
            cells = []
            for r in RR:
                s = sds.split([x for k in pc.INDICES for x in got[k][(a, w, r)]])
                di, do = s["is"]["total"] - TODAY[0], s["oos"]["total"] - TODAY[1]
                star = di > 0 and do > 0
                wins += star
                cells.append(f"{di:>+10,.0f} /{do:>+9,.0f}{'*' if star else ' '}")
            print(f"   {w} candles      " + "".join(f"{x:>26s}" for x in cells))
    print(f"\n {wins} of {len(ADX) * len(SWING) * len(RR)} settings beat today's system in BOTH periods.")
    best = (20.0, 5, 2.5)
    s = sds.split([x for k in pc.INDICES for x in got[k][best]])
    print(f" The pasted cell (ADX 20, last 5 candles, 2.5R): {s['is']['total']:+,.0f} / {s['oos']['total']:+,.0f} - "
          f"reproduces pasted_combos_study.py's +544,405 / +142,079" if (round(s['is']['total']), round(s['oos']['total'])) == (544405, 142079)
          else f" WARNING: the pasted cell gives {s['is']['total']:+,.0f} / {s['oos']['total']:+,.0f}, not +544,405 / +142,079")


if __name__ == "__main__":
    main()
