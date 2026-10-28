#!/usr/bin/env python3
"""best_target_study's chooser on made-up trades: the levels between T1 and T3, the half-years, the 12 kinds of trade,
learning only on what keep() allows, and the fallback for a kind with too few trades. No network."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

import pandas as pd
import best_target_study as b

print("1. LEVELS AND HALF-YEARS")
tr = {"t1": 105.0, "t2": 110.0, "t3": 120.0}
check("five levels from T1 to T3, halfway between each",
      [b.level_px(tr, lv) for lv in b.LEVELS] == [105.0, 107.5, 110.0, 115.0, 120.0])
ts = lambda d: pd.Timestamp(d, tz="Asia/Kolkata")
check("half-years start Aug 2020", [b.half_year(ts(d)) for d in ("2020-08-03", "2021-01-29", "2021-02-01",
                                                                 "2021-08-02", "2025-08-15", "2026-10-26")]
      == [0, 0, 1, 2, 10, 12])
check("three stop plans: the user's first close, a fixed stop, today's steps",
      b.PLANS == {"first close": (False, True), "fixed stop": (False, False), "today's steps": (True, False)})

print("2. THE KIND OF TRADE")
f = b.feats({"adx": 27.0, "when": ts("2024-03-04 12:15"), "t3": 130.0, "entry": 100.0, "risk": 20.0})
check("features known at the entry: ADX, minutes into the day, room in R", f == {"adx": 27.0, "mins": 735, "room": 1.5})
check("ADX 25-35, after 12:00, room over the cut", b.kind(f, 1.2) == (1, 1, 1))
check("room under the cut", b.kind(f, 2.0) == (1, 1, 0))
check("12 kinds", len(b.KINDS) == 12 and len(set(b.KINDS)) == 12)

print("3. LEARNING")
def row(day, adx, mins, room, net):
    return {"when": ts(day), "adx": adx, "mins": mins, "room": room, "net": net}
# strong-ADX morning trades with room pay at T3; everything else pays at T1. 40 of each kind before the cut,
# and after the cut the opposite (which learning must not see).
by = {lv: [] for lv in b.LEVELS}
for n in range(40):
    for lv in b.LEVELS:
        far = lv == "T3"
        by[lv].append(row("2024-01-10", 40.0, 600, 3.0, 100.0 if far else 10.0))
        by[lv].append(row("2024-01-10", 20.0, 780, 1.0, 10.0 if far else (50.0 if lv == "T1" else 5.0)))
        by[lv].append(row("2025-09-10", 40.0, 600, 3.0, -500.0 if far else 500.0))
keep = lambda r: r["when"] < ts("2025-08-15")
cut, table, fallback = b.fit(by, keep)
check("room cut = median room of the learned trades", cut == 2.0, cut)
check("strong ADX, morning, more room -> T3", table[(2, 0, 1)] == "T3")
check("weak ADX, afternoon, less room -> T1", table[(0, 1, 0)] == "T1")
check("a kind never seen takes the overall best (fallback)", table[(1, 0, 0)] == fallback, (fallback, table[(1, 0, 0)]))
check("the held-out rows never reached the choice", fallback == "T3" and table[(2, 0, 1)] == "T3")
few = {lv: [r for r in by[lv] if r["adx"] == 40.0][:29 * 2] for lv in b.LEVELS}
check("fewer than 30 trades of a kind -> the fallback",
      b.fit({lv: [r for r in few[lv] if keep(r)][:29] for lv in b.LEVELS}, keep)[1][(2, 0, 1)]
      == b.fit({lv: [r for r in few[lv] if keep(r)][:29] for lv in b.LEVELS}, keep)[2])
check("choose() reads the table", b.choose((cut, table, fallback),
                                           {"adx": 40.0, "when": ts("2025-09-10 10:00"), "t3": 160.0, "entry": 100.0,
                                            "risk": 20.0}) == "T3")

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
