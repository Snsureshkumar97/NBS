#!/usr/bin/env python3
"""one_target_study's exits on made-up candles: the first-close move to entry (only when in profit, only at the first
close), one target with nothing else moving the stop, and today's walk reproduced exactly. No network."""
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
import one_target_study as ots
import t3_exit_recheck_study as t3


def bars(rows):
    """rows of (high, low, close) after an entry bar at index 0; the day ends at the last row."""
    hi, lo, cl = zip(*([(100.0, 100.0, 100.0)] + rows))
    end = [False] * len(cl)
    end[-1] = True
    return {"hi": np.array(hi), "lo": np.array(lo), "cl": np.array(cl), "end": np.array(end)}


CE = {"side": "CE", "entry": 100.0, "stop": 90.0, "t1": 105.0, "t2": 110.0, "t3": 115.0}
PE = {"side": "PE", "entry": 100.0, "stop": 110.0, "t1": 95.0, "t2": 90.0, "t3": 85.0}
NOST = np.full(40, np.nan)

print("1. THE FIRST CLOSE")
A = bars([(103, 99, 102), (101, 99.5, 100.5)])
check("a call closing above entry at the first close: stopped at entry on the next touch",
      ots.sim(A, 0, CE, NOST, "t3", steps=False, first_close=True)[:2] == ([(100.0, 2, 1.0)], "stop at entry"))
A = bars([(101, 97, 98), (101, 96, 99), (100, 95, 97)])
check("a call closing below entry: the stop stays at 90, out at the day's close",
      ots.sim(A, 0, CE, NOST, "t3", steps=False, first_close=True)[:2] == ([(97.0, 3, 1.0)], "close"))
A = bars([(101, 97, 98), (104, 99, 103), (101, 99, 99.5)])
check("only the FIRST close counts: in profit at the second close moves nothing",
      ots.sim(A, 0, CE, NOST, "t3", steps=False, first_close=True)[:2] == ([(99.5, 3, 1.0)], "close"))
A = bars([(101, 96, 97), (101, 99, 100.5)])
check("a put closing below entry: stopped at entry when it comes back",
      ots.sim(A, 0, PE, NOST, "t3", steps=False, first_close=True)[:2] == ([(100.0, 2, 1.0)], "stop at entry"))
A = bars([(103, 99, 102), (101, 99.5, 100.5)])
check("without the first close the same call is not stopped",
      ots.sim(A, 0, CE, NOST, "t3", steps=False, first_close=False)[:2] == ([(100.5, 2, 1.0)], "close"))

print("2. ONE TARGET")
A = bars([(106, 101, 105.5), (107, 101, 102), (116, 101, 114)])
check("T1 touched moves nothing: out at T3",
      ots.sim(A, 0, CE, NOST, "t3", steps=False, first_close=True)[:2] == ([(115.0, 3, 1.0)], "target"))
check("today's steps: the same touch of T1 then 101 is out at T1",
      ots.sim(A, 0, CE, NOST, "t3", steps=True)[:2] == ([(105.0, 2, 1.0)], "stop"))
check("no target: held to the day's close", ots.sim(A, 0, CE, NOST, None, steps=False, first_close=True)[:2]
      == ([(114.0, 3, 1.0)], "close"))
check("a T2 target", ots.sim(A, 0, CE, NOST, "t2", steps=False)[:2] == ([(110.0, 3, 1.0)], "target"))

print("3. TODAY REPRODUCED")
rng = random.Random(7)
same = 0
for _ in range(3000):
    n = rng.randint(3, 30)
    cl = 100 + np.cumsum([0.0] + [rng.gauss(0, 2) for _ in range(n)])
    hi, lo = cl + np.abs([rng.gauss(0, 1.5) for _ in cl]), cl - np.abs([rng.gauss(0, 1.5) for _ in cl])
    end = np.zeros(len(cl), bool)
    end[-1] = True
    A = {"hi": hi, "lo": lo, "cl": cl, "end": end}
    st = np.where(np.arange(len(cl)) % 3 == 0, np.nan, cl - 3)
    tr = rng.choice((CE, PE))
    if tr is PE:
        st = np.where(np.isnan(st), np.nan, cl + 3)
    same += ots.sim(A, 0, tr, st, "t3") == t3.sim(A, 0, tr, st, "t3", step_t2=True)
check("sim(steps=True) is t3_exit_recheck_study.sim on 3,000 random walks", same == 3000, same)

print("4. THE BIG CANDLE'S WALK")
import indian_strategies_study as iss
idx = pd.date_range("2025-03-03 09:15", periods=8, freq="15min", tz="Asia/Kolkata")
df = pd.DataFrame({"Open": 100.0, "High": [100, 103, 101, 101, 101, 101, 101, 101],
                   "Low": [100, 99.5, 99, 99, 99, 99, 99, 99], "Close": [100, 102, 100.5, 100, 100, 100, 100, 100]},
                  index=idx)
walk = ots.walk_first_close(iss.walk)
check("first close above entry, then back to it: out at entry", walk(df, 0, "CE", 95.0, 110.0) == (100.0, 2))
check("the plain walk holds on", iss.walk(df, 0, "CE", 95.0, 110.0) != (100.0, 2))
plain = sum(ots.walk_first_close(iss.walk)(df, 0, s, st, tg) == iss.walk(df, 0, s, st, tg)
            for s, st, tg in (("CE", 99.6, 101), ("PE", 104, 98)))
check("no move when the first close is not in profit or the trade is already out", plain == 2)

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
