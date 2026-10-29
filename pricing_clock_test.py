#!/usr/bin/env python3
"""No option in the studies' model gains value from the clock alone (far_expiry_check.py, 29 Oct 2026: the 14+ day
cell let a far option gain Rs 1,009 a lot from 09:45 to 14:45 with the index unchanged, and the six-year 14+ day
result was mostly that). The shipped table passes pricing_calibration.clock_gains; the guard catches the old table;
fit() pools a row that would gain. No network."""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pricing
import pricing_calibration as pc

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE SHIPPED TABLE")
check("no days-to-expiry band lets an option held 2 hours or more gain over 1% from the clock alone",
      pc.clock_gains(pricing.SIGMA_RATIO) == set(), sorted(pc.clock_gains(pricing.SIGMA_RATIO)))
check("...the 14+ day band is one ratio all day", len({pricing.SIGMA_RATIO[(7, t)] for t in range(4)}) == 1,
      [pricing.SIGMA_RATIO[(7, t)] for t in range(4)])

print("2. THE GUARD")
old = {**pricing.SIGMA_RATIO, (7, 0): 0.872, (7, 1): 0.894, (7, 2): 0.914, (7, 3): 0.919}
check("the 28 Oct table's 14+ day band is caught", pc.clock_gains(old) == {7}, sorted(pc.clock_gains(old)))
steep = {**pricing.SIGMA_RATIO, (6, 0): 0.80, (6, 1): 0.88, (6, 2): 0.96, (6, 3): 1.04}
check("so is a 7-14 day band that climbs 30% through the day", 6 in pc.clock_gains(steep), sorted(pc.clock_gains(steep)))

print("3. FIT POOLS IT")
rng = np.random.default_rng(3)
rows = []
for dc, (lo, hi) in enumerate(zip(pricing.DTE_EDGES[:-1], pricing.DTE_EDGES[1:])):
    for tc, (a, b) in enumerate(zip(pricing.TOD_EDGES[:-1], pricing.TOD_EDGES[1:])):
        base = pricing.SIGMA_RATIO.get((dc, tc), 1.0) if dc != 7 else 0.85 + 0.03 * tc     # 14+ rising all day
        for _ in range(40):
            rows.append({"dte": rng.uniform(lo, min(hi, 60)), "tod": rng.uniform(a, b - 1), "ratio": base})
df = pd.DataFrame(rows)
t = pc.fit(df)
row7 = float(df[(df["dte"] >= 14)]["ratio"].median())
check("a rising 14+ day row is pooled to its median all day", all(t[(7, tc)] == round(row7, 3) for tc in range(4)),
      [t[(7, tc)] for tc in range(4)])
check("...and the bands that pass keep their time of day", t[(0, 3)] == round(pricing.SIGMA_RATIO[(0, 3)], 3)
      and t[(0, 0)] != t[(0, 3)])
check("the fitted table passes the guard", pc.clock_gains(t) == set())

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
