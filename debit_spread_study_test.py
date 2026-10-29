#!/usr/bin/env python3
"""debit_spread_study on made-up trades and a made-up recording: the sold strike, a spread = the bought leg + the
sold leg (each its own charges and slippage), NAKED = the harness's own price, a worthless sold leg is not sold, the
spread's cap, and a stale recorded price for the sold leg is refused. No network."""
import datetime as dt
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
import debit_spread_study as dss
import pricing
import pro_study as ps
import regime_study as rs
import vwap_ema_atr_study as vea

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE SOLD STRIKE")
check("a call sells the strike nearest its target", dss.short_strike(24000, 24137, 50, True) == 24150)
check("...at least one strike further out", dss.short_strike(24000, 24010, 50, True) == 24050)
check("a put mirrors it", dss.short_strike(24000, 23862, 50, False) == 23850
      and dss.short_strike(24000, 23990, 50, False) == 23950)

print("2. THE MODEL")
IST = "Asia/Kolkata"
d0 = dt.date(2026, 10, 5)
days = {d0 + dt.timedelta(days=i) for i in range(60) if (d0 + dt.timedelta(days=i)).weekday() < 5}
T = lambda h, m: pd.Timestamp(dt.datetime.combine(d0, dt.time(h, m)), tz=IST)
trade = lambda exit_spot, side="CE": {"index": "NIFTY", "side": side, "when": T(10, 0), "exit_time": T(11, 30),
                                      "entry_spot": 24000.0, "exit_spot": exit_spot, "sigma": 0.13,
                                      "t1": 24060.0 if side == "CE" else 23940.0, "t2": 24120.0 if side == "CE" else 23880.0,
                                      "t3": 24180.0 if side == "CE" else 23820.0}
ps.SLIP = dss.SLIP
pricing.MIN_DTE = 0
r = trade(24150.0)
naked = dss.model_net(r, days, "NAKED")
check("NAKED is the harness's own price", abs(naked - vea.price_trade("NIFTY", days, r["when"], 24000.0, r["exit_time"],
                                                                     24150.0, 0.13, True)) < 1e-6, naked)
qty, exp = config.INSTRUMENTS["NIFTY"]["lot_size"], ps.trade_expiry("NIFTY", d0, days)
q0 = pricing.premium(24000.0, 24200, exp, r["when"], 0.13, True)
q1 = pricing.premium(24150.0, 24200, exp, r["exit_time"], 0.13, True)
want = naked + rs.net_rupees(q1, q0, qty, "NSE", dss.SLIP)
check("a T3 spread = the bought leg + the sold 24,200 call (sold at entry, bought back at exit)",
      abs(dss.model_net(r, days, "T3") - want) < 1e-6, (dss.model_net(r, days, "T3"), want))
check("a winner earns less as a spread", dss.model_net(r, days, "T3") < naked)
loser = trade(23940.0)
check("a loser loses less as a spread", dss.model_net(loser, days, "T3") > dss.model_net(loser, days, "NAKED"))
put = trade(23850.0, "PE")
check("a put spread works the same way", dss.model_net(put, days, "T3") < dss.model_net(put, days, "NAKED")
      and dss.model_net(trade(24060.0, "PE"), days, "T3") > dss.model_net(trade(24060.0, "PE"), days, "NAKED"))
big = dict(trade(24600.0), exit_time=pd.Timestamp(dt.datetime.combine(exp, dt.time(15, 15)), tz=IST))
gross_cap = (24200 - 24000) * qty
check("the spread's profit is capped at its width", dss.model_net(big, days, "T3") < gross_cap
      and dss.model_net(big, days, "NAKED") > gross_cap)
far = dict(r, t3=26000.0)
check("a sold leg worth under Rs 1 is not sold: the trade stays NAKED",
      dss.model_net(far, days, "T3") == naked, (dss.model_net(far, days, "T3"), naked))
pricing.MIN_DTE = 14
check("14+ days: a later expiry, the same rule", ps.trade_expiry("NIFTY", d0, days) >= d0 + dt.timedelta(days=14)
      and dss.model_net(r, days, "T3") is not None)
pricing.MIN_DTE = 0

print("3. RECORDED PRICES")
mins = pd.date_range(T(9, 15), T(15, 29), freq="1min")
def series(price_at):
    return pd.DataFrame({"open": [price_at(t) for t in mins], "close": [price_at(t) for t in mins]}, index=mins)
class Fake:
    def __init__(self, short):
        self.short = short
    def expiries(self, k, d):
        return ["2026-10-06"]
    def option(self, k, d, e, K, side):
        return series(lambda t: 120.0 if t <= T(10, 0) else 180.0) if K == 24000 else self.short
    def index(self, k, d):
        return None
liquid = series(lambda t: 30.0 if t <= T(10, 0) else 60.0)
rr = dict(r, how="close")
got = dss.real_net(rr, Fake(liquid), 0, "T3")
nk = dss.real_net(rr, Fake(liquid), 0, "NAKED")
check("NAKED on the recording: bought at 10:00's open, sold at the exit minute's close",
      abs(nk - rs.net_rupees(120.0, 180.0, qty, "NSE", dss.SLIP)) < 1e-6, nk)
check("the spread adds the sold leg's own recorded round trip",
      abs(got - (nk + rs.net_rupees(60.0, 30.0, qty, "NSE", dss.SLIP))) < 1e-6, got)
stale = series(lambda t: 30.0).loc[:T(9, 40)]                 # last traded at 09:40
check("a sold leg with no candle in the 5 minutes before entry is refused", dss.real_net(rr, Fake(stale), 0, "T3") is None)
s = series(lambda t: 30.0)
gapped = s[(s.index <= T(9, 58)) | (s.index >= T(11, 0))]
check("fresh(): a close 2 minutes old is used, 6 minutes old is not",
      dss.fresh(gapped, T(10, 0), "open") == 30.0 and dss.fresh(gapped, T(10, 4), "close") is None)

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
