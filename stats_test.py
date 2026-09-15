#!/usr/bin/env python3
"""pro_study.stats: the drawdown every study reports, checked by hand."""
import datetime as dt

import pro_study as ps

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

t = lambda h: dt.datetime(2026, 1, 5, h)
r = lambda h, net, k="NIFTY": {"when": t(h), "net": net, "index": k}

check("a period that opens with a loss counts it", ps.stats([r(10, -100), r(11, 50)])["dd"] == 100)
check("a win then a loss: the fall from the peak", ps.stats([r(10, 100), r(11, -150), r(12, 20)])["dd"] == 150)
index_order = [r(10, 100, "NIFTY"), r(12, -80, "NIFTY"), r(11, 100, "SENSEX"), r(13, -80, "SENSEX")]
# In time order: +100 (10:00), +100 (11:00), -80 (12:00), -80 (13:00) -> peak 200, trough 40 -> 160.
# Walked in list order it was +100, -80, +100, -80 -> never more than 80.
check("losing runs on different indices that overlap in time add up", ps.stats(index_order)["dd"] == 160, ps.stats(index_order)["dd"])
check("totals and profit factor do not depend on order", ps.stats(index_order)["total"] == 40 and round(ps.stats(index_order)["pf"], 2) == 1.25)
check("all winners: no drawdown", ps.stats([r(10, 10), r(11, 20)])["dd"] == 0)
print("STATS TEST PASSED" if not fails else f"STATS TEST FAILED: {fails}")
