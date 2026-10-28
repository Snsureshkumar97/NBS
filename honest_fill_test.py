#!/usr/bin/env python3
"""The walks must not pay a coin (fills.py, found 28 Oct 2026). On made-up random-walk candles - no edge anywhere -
a coin-flip trader through the rules' exit (stop steps at T1 / T2, a trailed line that sometimes jumps to the far side
of the price, out at T3) must not make money in points; filled the old way (every stop at its own price) it does, and
this test must see that. Also: the gap fill itself, in each walk. No network."""
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

import numpy as np
import pandas as pd
import fills
import indian_strategies_study as iss
import opening_window_study as ows
import t3_exit_recheck_study as t3

print("1. THE GAP FILL")
A = {"hi": np.array([100, 106, 104.0, 104]), "lo": np.array([100, 101, 98.0, 98]),
     "cl": np.array([100, 102, 99.0, 99]), "op": np.array([100, 100, 101.0, 99]), "end": np.array([0, 0, 0, 1], bool)}
CE = {"side": "CE", "entry": 100.0, "stop": 95.0, "t1": 105.0, "t2": 110.0, "t3": 115.0}
NOST = np.full(4, np.nan)
check("T1 touched, then the next candle opens below it: sold at that open, not at T1",
      t3.sim(A, 0, CE, NOST, "t3", step_t2=True)[0] == [(101.0, 2, 1.0)], t3.sim(A, 0, CE, NOST, "t3", step_t2=True)[0])
check("the same in simulate_live", ows.simulate_live(A, 0, CE, NOST, "t3") == [(101.0, 2, 1.0)])
A2 = dict(A, op=np.array([100, 100, 105.5, 99]))
check("a candle that opens above the stop and falls through it: sold at the stop",
      t3.sim(A2, 0, CE, NOST, "t3", step_t2=True)[0] == [(105.0, 2, 1.0)])
check("no open given: the previous close stands in", fills.stop_fill({"cl": np.array([100.0, 102.0])}, 1, 105.0, True)
      == 100.0 and fills.stop_fill({"cl": np.array([100.0, 102.0])}, 1, 95.0, False) == 100.0)
idx = pd.date_range("2025-03-03 09:15", periods=4, freq="15min", tz="Asia/Kolkata")
bars = pd.DataFrame({"Open": [100, 100, 93, 94.0], "High": [100, 101, 94, 95.0], "Low": [100, 99, 92, 93.0],
                     "Close": [100, 100.5, 93, 94.0]}, index=idx)
check("indian_strategies_study.walk: a candle opening below the stop sells at that open",
      iss.walk(bars, 0, "CE", 95.0, 110.0) == (93.0, 2))

print("2. A COIN ON RANDOM CANDLES")
rng = random.Random(11)


def day(n=26):
    cl, op, hi, lo = [1000.0], [1000.0], [1000.0], [1000.0]
    for _ in range(n - 1):
        o = cl[-1]
        c = o + rng.gauss(0, 3)
        op.append(o); cl.append(c)
        hi.append(max(o, c) + abs(rng.gauss(0, 1.5))); lo.append(min(o, c) - abs(rng.gauss(0, 1.5)))
    end = [False] * n
    end[-1] = True
    return {k: np.array(v) for k, v in (("op", op), ("hi", hi), ("lo", lo), ("cl", cl))} | {"end": np.array(end)}


def pts(walk, old):
    out = []
    for _ in range(6000):
        A = day()
        ce = rng.random() < 0.5
        i = rng.randint(1, 15)
        e, s = A["cl"][i], 1 if ce else -1
        tr = {"side": "CE" if ce else "PE", "entry": e, "stop": e - 6 * s, "t1": e + 3 * s, "t2": e + 6 * s,
              "t3": e + 9 * s}
        # a trail that now and then jumps to the far side of the price (a Supertrend flip)
        st = np.where(np.array([rng.random() < 0.15 for _ in A["cl"]]), A["cl"] + 2 * s, A["cl"] - 4 * s)
        if old:                                       # the old fill: every stop at its own price
            A = dict(A, op=np.full(len(A["cl"]), math.inf if ce else -math.inf))
        px = walk(A, i, tr, st)
        out.append((px - e) * s)
    a = np.array(out)
    return a.mean(), a.std() / math.sqrt(len(a))


for name, walk in (("rules exit (t3_exit_recheck_study.sim, steps at T1 and T2)",
                    lambda A, i, tr, st: t3.sim(A, i, tr, st, "t3", step_t2=True)[0][-1][0]),
                   ("simulate_live", lambda A, i, tr, st: ows.simulate_live(A, i, tr, st, "t3")[-1][0])):
    m, se = pts(walk, old=False)
    check(f"{name}: a coin makes nothing (mean {m:+.3f} pts, se {se:.3f})", m < 3 * se)
    m0, se0 = pts(walk, old=True)
    check(f"{name}: the old fill paid the coin (mean {m0:+.3f} pts) - this test would catch it", m0 > 6 * se0)

print("3. THE BIG CANDLE / CAMARILLA WALK (indian_strategies_study.walk: a fixed stop and target)")
times = pd.date_range("2025-03-03 09:15", periods=26, freq="15min", tz="Asia/Kolkata")


def walk_pts(old):
    out = []
    for _ in range(2500):
        A = day()
        ce = rng.random() < 0.5
        i = rng.randint(1, 15)
        e, s = A["cl"][i], 1 if ce else -1
        op = np.full(26, math.inf if ce else -math.inf) if old else A["op"]
        bars = pd.DataFrame({"Open": op, "High": A["hi"], "Low": A["lo"], "Close": A["cl"]}, index=times)
        px, _ = iss.walk(bars, i, "CE" if ce else "PE", e - 6 * s, e + 12 * s)
        out.append((px - e) * s)
    a = np.array(out)
    return a.mean(), a.std() / math.sqrt(len(a))


m, se = walk_pts(old=False)
check(f"a coin makes nothing (mean {m:+.3f} pts, se {se:.3f})", m < 3 * se)
print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
