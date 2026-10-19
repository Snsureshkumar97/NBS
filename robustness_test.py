#!/usr/bin/env python3
"""robustness.py: the walk-forward and Monte Carlo checks every verdict now carries (the user, 20 Oct 2026: "yes add
those two checks"). Hand-made trades with known answers. No network, no history."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import robustness as rb

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def trades(per_day, start="2024-01-02", days=500, extra=None):
    """One trade a weekday: per_day(n) rupees on day n; extra(n) adds a second trade that day."""
    out = []
    for n, d in enumerate(pd.bdate_range(start, periods=days, tz="Asia/Kolkata")):
        out.append({"when": d + pd.Timedelta(hours=10), "net": float(per_day(n))})
        if extra and extra(n) is not None:
            out.append({"when": d + pd.Timedelta(hours=13), "net": float(extra(n))})
    return out


SPLIT = pd.Timestamp("2025-01-01", tz="Asia/Kolkata")

print("1. DAYS, HALF-YEARS")
t = trades(lambda n: 100, days=10, extra=lambda n: 50 if n == 0 else None)
d = rb.daily(t)
check("two trades on a day are one day's result", len(d) == 10 and d.iloc[0] == 150, d.iloc[:2].tolist())
h = rb.by_half(trades(lambda n: 1, start="2024-06-27", days=4))
check("half-years split at 1 July", list(h.index) == ["2024-H1", "2024-H2"], list(h.index))

print("2. MONTE CARLO")
flat_win = rb.monte_carlo(trades(lambda n: 100))
check("a system that wins every day: no drop, no chance of a loss",
      flat_win["dd_p95"] == 0 and flat_win["loss_chance"] == 0, flat_win)
coin = rb.monte_carlo(trades(lambda n: 100 if n % 2 else -100))
check("a coin flip that ends level: a real drop, and a loss about half the time",
      coin["dd_p95"] > coin["dd_median"] > 0 and 0.35 < coin["loss_chance"] < 0.65, coin)
check("the same seed gives the same answer", rb.monte_carlo(trades(lambda n: 100 if n % 2 else -100)) == coin)
edge = rb.monte_carlo(trades(lambda n: 160 if n % 2 else -100))
check("a real edge: a smaller chance of a loss than the coin flip", edge["loss_chance"] < 0.05, edge)

print("3. WALK-FORWARD")
base = trades(lambda n: 100 if n % 2 else -90)
steady = trades(lambda n: 110 if n % 2 else -90)                    # a little better every half-year
lucky = trades(lambda n: (2000 if n == 10 else 0) + (100 if n % 2 else -95))   # one big day early, a bit worse after
wf_s, wf_l = rb.walk_forward(steady, base), rb.walk_forward(lucky, base)
check("better every half-year: wins all of them", wf_s["wins"] == wf_s["of"] and wf_s["of"] >= 3, wf_s)
check("one lucky day: wins one half-year only", wf_l["wins"] == 1, wf_l)
line_s = rb.verdict_line(steady, base, SPLIT, passed=True)
line_l = rb.verdict_line(lucky, base, SPLIT, passed=True)
check("the steady one reads STEADY", "STEADY" in line_s and "FRAGILE" not in line_s, line_s)
check("the lucky one reads KEEP, FRAGILE - wins too few half-years", "KEEP, FRAGILE" in line_l
      and "wins too few half-years" in line_l, line_l)
check("a drop gets no STEADY / FRAGILE mark", "STEADY" not in rb.verdict_line(steady, base, SPLIT, passed=False)
      and "FRAGILE" not in rb.verdict_line(steady, base, SPLIT, passed=False))
wild = trades(lambda n: 400 if n % 2 else -350)                     # more profit, much deeper swings
check("more profit with far deeper swings: FRAGILE (deeper 95% drop)",
      "deeper 95% drop" in rb.verdict_line(wild, base, SPLIT, passed=True))

print("4. WALK-FORWARD PICK")
first = trades(lambda n: 100 if n < 250 else -50)                   # good early, bad later
later = trades(lambda n: -50 if n < 250 else 100)                   # the reverse
p = rb.walk_forward_pick({"first": first, "later": later})
check("re-choosing from the past year chases what already stopped working",
      p["picked"] < max(p["fixed"].values()), p["picked"])
p2 = rb.walk_forward_pick({"good": steady, "base": base})
check("a setting better every half-year is the one picked", all(n == "good" for _, n in p2["choices"])
      and abs(p2["picked"] - p2["fixed"]["good"]) < 1e-6)

print("5. THE SHARED VERDICT CARRIES IT")
import stop_day_study as sds
v = sds.verdict("test", [dict(r, when=r["when"]) for r in steady], base)
check("stop_day_study.verdict prints the walk-forward line under the verdict", "\n" in v and "walk-forward" in v, v[:60])

print()
print("ROBUSTNESS TEST PASSED" if not fails else f"ROBUSTNESS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
