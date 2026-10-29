#!/usr/bin/env python3
"""far_expiry_check on a made-up recording: the far contract is the first expiry 14+ days out, the real and model
prices come from that same contract at the same minutes, a stale price is refused, and the error line adds up.
No network."""
import datetime as dt
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
import far_expiry_check as fec
import pricing
import regime_study as rs

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE FAR CONTRACT")
d = dt.date(2026, 10, 12)
check("the first expiry at least 14 days out", fec.far_expiry(["2026-10-27", "2026-11-24"], d) == "2026-10-27")
check("...13 days is not enough", fec.far_expiry(["2026-10-25", "2026-11-24"], d) == "2026-11-24")
check("...and none when nothing recorded is that far", fec.far_expiry(["2026-10-14", "2026-10-20"], d) is None)

print("2. REAL AND MODEL ON ONE CONTRACT")
IST = "Asia/Kolkata"
T = lambda h, m: pd.Timestamp(dt.datetime.combine(d, dt.time(h, m)), tz=IST)
mins = pd.date_range(T(9, 15), T(15, 29), freq="1min")
series = lambda f: pd.DataFrame({"open": [f(t) for t in mins], "close": [f(t) for t in mins]}, index=mins)
class Fake:
    def __init__(self, book):
        self.book, self.asked = book, []
    def expiries(self, k, dd):
        return ["2026-10-14", "2026-10-27", "2026-11-24"]
    def option(self, k, dd, e, K, side):
        self.asked.append((e, K, side))
        return self.book.get((e, K))
    def index(self, k, dd):
        return None
book = {("2026-10-27", 57000): series(lambda t: 600.0 if t <= T(10, 0) else 700.0),
        ("2026-10-14", 57000): series(lambda t: 200.0 if t <= T(10, 0) else 260.0)}
r = {"index": "BANKNIFTY", "side": "CE", "when": T(10, 0), "exit_time": T(11, 30), "entry_spot": 57010.0,
     "exit_spot": 57200.0, "sigma": 0.14, "how": "close"}
P = Fake(book)
got = fec.both(r, P, far=True)
qty = config.INSTRUMENTS["BANKNIFTY"]["lot_size"]
check("REAL: the 27 Oct 57,000 call bought at 10:00 and sold at the exit minute",
      P.asked[-1] == ("2026-10-27", 57000, "CE") and abs(got[0] - rs.net_rupees(600.0, 700.0, qty, "NSE", fec.SLIP)) < 1e-6, got)
e = dt.date(2026, 10, 27)
want = rs.net_rupees(pricing.premium(57010.0, 57000, e, r["when"], 0.14, True),
                     pricing.premium(57200.0, 57000, e, r["exit_time"], 0.14, True), qty, "NSE", fec.SLIP)
check("MODEL: the same contract, the same minutes", abs(got[1] - want) < 1e-6 and got[4] == "2026-10-27", (got[1], want))
near = fec.both(r, Fake(book), far=False)
check("NEAREST: the 14 Oct contract", near and abs(near[0] - rs.net_rupees(200.0, 260.0, qty, "NSE", fec.SLIP)) < 1e-6
      and near[4] == "2026-10-14", near)
stale = {**book, ("2026-10-27", 57000): series(lambda t: 600.0).loc[:T(9, 40)]}
check("a far contract last traded 20 minutes before entry is refused", fec.both(r, Fake(stale), far=True) is None)

print("3. THE ERROR LINE")
text, mean = fec.err_line([(100.0, 150.0, 10.0, 11.0), (-50.0, -40.0, 20.0, 20.0)])
check("model - real, averaged", mean == 30.0 and "+30" in text and "median 1.05" in text, text)

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
