#!/usr/bin/env python3
"""itm_strike_study on made-up trades and a made-up recording: the strikes (in the money is below for a call, above
for a put), ATM = the harness's own price, an in-the-money option costs more and moves more, a stale recorded price
is refused, and the strikes are compared only on trades priced every way. No network."""
import datetime as dt
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
import debit_spread_study as dss
import itm_strike_study as its
import pricing
import pro_study as ps
import regime_study as rs

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE STRIKES")
check("a call's in-the-money strikes are below", its.strike(24000, 1, 50, True) == 23950 and its.strike(24000, 2, 50, True) == 23900)
check("a put's are above", its.strike(24000, 1, 50, False) == 24050 and its.strike(24000, 2, 50, False) == 24100)
check("one out of the money: the other way", its.strike(24000, -1, 50, True) == 24050 and its.strike(24000, -1, 50, False) == 23950)

print("2. THE MODEL")
IST = "Asia/Kolkata"
d0 = dt.date(2026, 10, 5)
days = {d0 + dt.timedelta(days=i) for i in range(60) if (d0 + dt.timedelta(days=i)).weekday() < 5}
T = lambda h, m: pd.Timestamp(dt.datetime.combine(d0, dt.time(h, m)), tz=IST)
trade = lambda exit_spot, side="CE": {"index": "NIFTY", "side": side, "when": T(10, 0), "exit_time": T(11, 30),
                                      "entry_spot": 24000.0, "exit_spot": exit_spot, "sigma": 0.13,
                                      "t1": 24060.0, "t2": 24120.0, "t3": 24180.0}
ps.SLIP = its.SLIP
pricing.MIN_DTE = 0
win, loss = trade(24150.0), trade(23940.0)
atm = its.model_net(win, days, 0)
check("ATM is debit_spread_study's NAKED (the harness's own price)", abs(atm[0] - dss.model_net(win, days, "NAKED")) < 1e-6)
itm = its.model_net(win, days, 2)
qty, exp = config.INSTRUMENTS["NIFTY"]["lot_size"], ps.trade_expiry("NIFTY", d0, days)
want = rs.net_rupees(pricing.premium(24000.0, 23900, exp, win["when"], 0.13, True),
                     pricing.premium(24150.0, 23900, exp, win["exit_time"], 0.13, True), qty, "NSE", its.SLIP)
check("ITM2 = the 23,900 call bought and sold at the trade's minutes", abs(itm[0] - want) < 1e-6, (itm[0], want))
check("an in-the-money lot costs more", itm[1] > atm[1] > its.model_net(win, days, -1)[1], (itm[1], atm[1]))
check("...makes more on a winner", itm[0] > atm[0])
check("...and loses more on a loser", its.model_net(loss, days, 2)[0] < its.model_net(loss, days, 0)[0])
put = trade(23850.0, "PE")
check("a put the same way", its.model_net(put, days, 2)[1] > its.model_net(put, days, 0)[1]
      and its.model_net(put, days, 2)[0] > its.model_net(put, days, 0)[0])

print("3. RECORDED PRICES")
mins = pd.date_range(T(9, 15), T(15, 29), freq="1min")
series = lambda f: pd.DataFrame({"open": [f(t) for t in mins], "close": [f(t) for t in mins]}, index=mins)
class Fake:
    def __init__(self, book):
        self.book = book
    def expiries(self, k, d):
        return ["2026-10-06"]
    def option(self, k, d, e, K, side):
        return self.book.get(K)
    def index(self, k, d):
        return None
book = {24000: series(lambda t: 120.0 if t <= T(10, 0) else 180.0),
        23950: series(lambda t: 150.0 if t <= T(10, 0) else 220.0)}
rr = dict(win, how="close")
got = its.real_net(rr, Fake(book), 0, 1)
check("ITM1 on the recording: its own prices at the trade's minutes",
      abs(got[0] - rs.net_rupees(150.0, 220.0, qty, "NSE", its.SLIP)) < 1e-6 and got[1] == 150.0 * qty, got)
stale = {**book, 23950: series(lambda t: 150.0).loc[:T(9, 30)]}
check("a strike that last traded 30 minutes before entry is refused", its.real_net(rr, Fake(stale), 0, 1) is None)

print("4. THE SAME TRADES EVERY WAY")
a = dict(win, s0={"ATM": (100.0, 5000.0), "ITM1": (150.0, 7000.0), "ITM2": (180.0, 9000.0), "OTM1": (50.0, 3000.0)})
b = dict(win, s0={"ATM": (100.0, 5000.0), "ITM1": None, "ITM2": (180.0, 9000.0), "OTM1": (50.0, 3000.0)})
check("a trade one way could not price is left out of every way", [r["net"] for r in its.as_rows([a, b], "s0", "ATM")] == [100.0]
      and [r["net"] for r in its.as_rows([a, b], "s0", "ITM2")] == [180.0])

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
