#!/usr/bin/env python3
"""Hand-traced checks for crypto_strategy_study.py's own pieces - the contract
expiry, Delta's fee, perp and option pricing, the live-exit walker, sequencing
and the breakout cross. Pure functions on hand-built inputs; no network."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import crypto_strategy_study as cs
import regime_study as rs

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


T = lambda s: pd.Timestamp(s, tz="Asia/Kolkata")

print("1. WHICH CONTRACT - Delta's BTC options settle at 17:30 IST")
check("16:00 entry: the nearest daily is the SAME day's 17:30",
      cs.expiry_for(T("2026-10-01 16:00"), "nearest") == T("2026-10-01 17:30"))
check("17:30 exactly: that one is settling now - the nearest is TOMORROW's",
      cs.expiry_for(T("2026-10-01 17:30"), "nearest") == T("2026-10-02 17:30"))
check("18:00 entry: nearest is tomorrow 17:30", cs.expiry_for(T("2026-10-01 18:00"), "nearest") == T("2026-10-02 17:30"))
check("next_day is one daily further out than nearest",
      cs.expiry_for(T("2026-10-01 16:00"), "next_day") == T("2026-10-02 17:30"))
check("monthly is 40 days out", cs.expiry_for(T("2026-10-01 16:00"), "monthly") == T("2026-11-10 16:00"))

print("2. DELTA'S OPTION FEE - 0.01% OF NOTIONAL, CAPPED AT 3.5% OF PREMIUM, PLUS 18% GST")
check("$600 premium at $84,000: notional fee 8.40 is under the 21.00 cap -> 8.40 x 1.18 = 9.912",
      abs(cs.option_fee(600, 84000) - 9.912) < 1e-9, cs.option_fee(600, 84000))
check("$100 premium: the 3.5% cap (3.50) binds -> 3.50 x 1.18 = 4.13",
      abs(cs.option_fee(100, 84000) - 4.13) < 1e-9, cs.option_fee(100, 84000))

print("3. PERPETUAL P&L - 0.05% TAKER EACH SIDE, PLUS GST")
check("long 84,000 -> 84,500: +500 less 0.0005 x 168,500 x 1.18 = 99.415 -> 400.585",
      abs(cs.price_perp("CE", 84000, 84500) - 400.585) < 1e-6, cs.price_perp("CE", 84000, 84500))
check("short the same move loses: -500 - 99.415", abs(cs.price_perp("PE", 84000, 84500) + 599.415) < 1e-6)

print("4. OPTION P&L - BLACK-SCHOLES BOTH ENDS, FEES BOTH ENDS, HALF THE SPREAD EACH END")
t0, t1 = T("2026-10-01 18:00"), T("2026-10-01 20:00")
pnl, p0 = cs.price_option("CE", 84000.0, t0, 84600.0, t1, 0.50, 0.50, "next_day")
exp = T("2026-10-03 17:30")
T0 = (exp - t0).total_seconds() / 3600 / cs.YEAR_H
T1 = (exp - t1).total_seconds() / 3600 / cs.YEAR_H
e0 = rs.bs(84000, 84000, T0, 0.5, True)
e1 = rs.bs(84600, 84000, T1, 0.5, True)
want = e1 - e0 - cs.option_fee(e0, 84000) - cs.option_fee(e1, 84600) - 0.01 * (e0 + e1)
check("entry premium is BS at strike 84,000 (84,000 rounds to the 200 grid) with 47.5h left",
      abs(p0 - e0) < 1e-9, round(p0, 2))
check("net = exit premium - entry premium - both fees - 1% of each premium", abs(pnl - want) < 1e-9,
      (round(pnl, 2), round(want, 2)))
# Expired before the exit: settles at intrinsic, no exit fee/spread.
t0, t1 = T("2026-10-01 16:00"), T("2026-10-01 20:00")
pnl, p0 = cs.price_option("CE", 84000.0, t0, 84100.0, t1, 0.50, 0.50, "nearest", settle_spot=85000.0)
want = 1000.0 - p0 - cs.option_fee(p0, 84000) - 0.01 * p0
check("a nearest-daily call that settles first is worth its intrinsic 1,000 at settlement - "
      "not the later exit price", abs(pnl - want) < 1e-9, (round(pnl, 2), round(want, 2)))
# 5 minutes before settlement an ATM call is still ~$52 (0.4 x 84,000 x 0.5 x sqrt(5min/yr));
# 30 seconds before, ~$16 - under the $20 floor.
check("a premium under $20 is refused (None) - a quote that small is noise",
      cs.price_option("CE", 84000.0, T("2026-10-01 17:29:30"), 84000.0, T("2026-10-01 17:29:45"),
                      0.5, 0.5, "nearest")[0] is None)
check("...while 5 minutes out (~$52) it is still priced",
      cs.price_option("CE", 84000.0, T("2026-10-01 17:25"), 84000.0, T("2026-10-01 17:26"),
                      0.5, 0.5, "nearest")[0] is not None)

print("5. THE LIVE EXIT WALKER - HAND-TRACED BARS (call: entry 100, stop 95, T1 102, T2 104)")
def bars(rows):
    hi = np.array([r[0] for r in rows], float); lo = np.array([r[1] for r in rows], float)
    cl = np.array([r[2] for r in rows], float)
    return hi, lo, cl
hi, lo, cl = bars([(100, 100, 100), (101, 99, 100), (102.5, 100, 102.3), (102.4, 101.9, 102)])
check("bar 2 touches T1 -> stop moves to 102; bar 3's low 101.9 hits it -> exit 102 'stop'",
      cs.walk_live(hi, lo, cl, 0, "CE", 100, 95, 102, 104) == (102, 3, "stop"))
hi, lo, cl = bars([(100, 100, 100), (104.5, 94, 100)])
check("one bar spans stop AND target: the stop is taken (pessimistic order)",
      cs.walk_live(hi, lo, cl, 0, "CE", 100, 95, 102, 104) == (95, 1, "stop"))
hi, lo, cl = bars([(100, 100, 100)] + [(101, 99.5, 100)] * 8 + [(100.5, 99.9, 100)])
check("8 bars without T1 -> stop to breakeven 100; bar 8 CLOSED at 100 (at the new stop) -> out at bar 8's close",
      cs.walk_live(hi, lo, cl, 0, "CE", 100, 95, 102, 104, be_bars=8) == (100, 8, "stop"))
hi, lo, cl = bars([(100, 100, 100)] + [(101, 99.5, 99.4)] * 8 + [(100.5, 99.9, 100)])
check("...and if bar 8 closed UNDER entry (99.4), breakeven puts the stop above the market: it closes at "
      "the MARKET (99.4), not at a 100 nobody could get",
      cs.walk_live(hi, lo, cl, 0, "CE", 100, 95, 102, 104, be_bars=8) == (99.4, 8, "stop"))
hi, lo, cl = bars([(100, 100, 100)] + [(101, 99.5, 100.6)] * 8 + [(100.9, 100.2, 100.5)])
check("...while a trade still above entry at bar 8 (100.6) just carries a breakeven stop on",
      cs.walk_live(hi, lo, cl, 0, "CE", 100, 95, 102, 104, be_bars=8, max_bars=9) == (100.5, 9, "time"))
# bar 2's low (102.1) stays ABOVE the T1 stop (102), so it is the trail, not T1, that bar 3 hits
hi, lo, cl = bars([(100, 100, 100), (102.2, 100, 102.1), (103, 102.1, 102.8), (103.1, 102.4, 103)])
st = np.array([np.nan, 99.0, 102.5, 102.6])
check("after T1 the Supertrend trail lifts the stop to 102.5 (above T1's 102); bar 3's low 102.4 "
      "exits at 102.5", cs.walk_live(hi, lo, cl, 0, "CE", 100, 95, 102, 104, st_line=st) == (102.5, 3, "stop"))
hi, lo, cl = bars([(100, 100, 100), (101, 99.5, 100.4), (101, 99.6, 100.2)])
opp = np.array([None, None, "PE"], dtype=object)
check("a bar closing on the clear opposite side exits at that close ('reversal')",
      cs.walk_live(hi, lo, cl, 0, "CE", 100, 95, 102, 104, opp=opp) == (100.2, 2, "reversal"))
check("a reading on the trade's OWN side is not a reversal",
      cs.walk_live(hi, lo, cl, 0, "CE", 100, 95, 102, 104, opp=np.array([None, None, "CE"], dtype=object),
                   max_bars=2) == (100.2, 2, "time"))
hi, lo, cl = bars([(100, 100, 100), (101, 97, 97.8), (100, 96.5, 97)])
check("put mirror: entry 100, stop 105, T1 98 touched on bar 1 -> stop 98; bar 2's high 100 hits it",
      cs.walk_live(hi, lo, cl, 0, "PE", 100, 105, 98, 96) == (98, 2, "stop"))

hi, lo, cl = bars([(100, 100, 100), (102.5, 100.5, 101.5)])
check("T1 touched and the SAME bar closes back under it: price passed back through T1, so it fills at "
      "T1 (102) on that bar - not at the 101.5 close",
      cs.walk_live(hi, lo, cl, 0, "CE", 100, 95, 102, 104) == (102, 1, "stop"))

print("6. SEQUENCING - ONE POSITION AT A TIME")
r = lambda w, x, s="CE": {"when": T(w), "exit_time": T(x), "side": s}
kept = cs.sequential([r("2026-10-01 10:00", "2026-10-01 11:00"), r("2026-10-01 10:30", "2026-10-01 12:00"),
                      r("2026-10-01 11:00", "2026-10-01 11:30", "PE")])
check("a trade entered while one is open is skipped; one entered at the exit moment is allowed",
      [k["when"] for k in kept] == [T("2026-10-01 10:00"), T("2026-10-01 11:00")], [k["when"] for k in kept])

print("7. THE BREAKOUT FIRES ON THE CROSS, NOT ON EVERY BAR ABOVE THE LEVEL")
n = 60
idx = pd.date_range("2026-09-01 00:00", periods=n, freq="15min", tz="Asia/Kolkata")
# Flat, then a steady climb of 2 a bar with highs only 0.5 above the close: from bar 40 on, EVERY
# close is above the prior 20 bars' high - so only the cross itself must fire, not all 20 bars.
c = np.array([100.0] * 40 + [100.0 + 2 * k for k in range(1, 21)])
df = pd.DataFrame({"Open": c, "High": c + 0.5, "Low": c - 0.5, "Close": c, "Volume": [10.0] * n}, index=idx)
fund = pd.Series(0.0, index=idx)
room = np.zeros(n)
ents = cs.breakout_entries(df, fund, room)
check("a climb that closes above the prior 20-bar high on 20 straight bars is ONE entry (bar 40), not twenty",
      len(ents) == 1 and ents[0][0] == 40 and ents[0][1] == "CE", ents)
e = ents[0]
check("stop is 1.5 x ATR under the close and the target exactly 2R above it",
      abs((e[4] - e[2]) - 2 * (e[2] - e[3])) < 1e-9)
check("the volume vote blocks it when the breakout candle's volume is only 1x the prior 10",
      cs.breakout_entries(df, fund, room, filters=("vol",)) == [])

print("8. PREMIUM-TRACKED LEVELS - AS build_recommendation() FREEZES THEM FOR A LIVE QUOTE")
tr = {"side": "CE", "entry": 84000.0, "t1": 84200.0, "t2": 84400.0, "t3": 84600.0, "stop": 83800.0,
      "target_basis": "market_reach"}
tg, sl = cs.premium_levels(tr, 1000.0)
check("market-reach basis: targets = premium + index distance x 0.5 -> 1100/1200/1300, stop 1000 - 100 = 900",
      tg == [1100.0, 1200.0, 1300.0] and sl == 900.0, (tg, sl))
tg, sl = cs.premium_levels(dict(tr, target_basis="risk_multiple"), 1000.0)
check("otherwise PREMIUM_TARGET_PCTS 20/40/75% and PREMIUM_SL_PCT 25% of the premium -> 1200/1400/1750, stop 750",
      [round(x, 6) for x in tg] == [1200.0, 1400.0, 1750.0] and abs(sl - 750.0) < 1e-9, (tg, sl))

print("9. THE PREMIUM WALKER - THE STOP AND TARGETS ARE ON THE OPTION'S OWN PRICE")
idx = pd.date_range("2026-10-01 18:00", periods=12, freq="15min", tz="Asia/Kolkata")
close_t = idx + pd.Timedelta(minutes=15)
ivs = np.full(12, 0.5)
# A call bought at bar 0's close (84,000). Bar 1's high 84,400 lifts the premium past T1's level
# (premium at entry + 200 x 0.5 = +100) -> stop moves to T1's premium. Bar 2 trades down to 84,000,
# where the premium (an hour of decay later) is below T1's premium -> exits AT T1's premium.
hi = np.array([84000, 84400, 84150] + [84000] * 9, float)
lo = np.array([84000, 84100, 84000] + [84000] * 9, float)
cl = np.array([84000, 84300, 84050] + [84000] * 9, float)
tr = {"side": "CE", "entry": 84000.0, "t1": 84200.0, "t2": 84800.0, "t3": 85000.0, "stop": 83600.0,
      "target_basis": "market_reach"}
got = cs.walk_live_premium(hi, lo, cl, ivs, close_t, 0, tr, "next_day")
exp = cs.expiry_for(close_t[0], "next_day")
yrs = lambda t: (exp - t).total_seconds() / 3600 / cs.YEAR_H
p0 = rs.bs(84000, 84000, yrs(close_t[0]), 0.5, True)
check("entry premium is Black-Scholes at the entry's own bar", abs(got[0] - p0) < 1e-9, round(got[0], 2))
check("T1 touched on bar 1, then bar 2's low takes the premium back under T1's level -> exit at "
      "T1's premium (entry + 100), bar 2, 'stop'", abs(got[1] - (p0 + 100)) < 1e-9 and got[2] == 2 and got[3] == "stop",
      (round(got[1], 2), got[2], got[3]))
# Breakeven on the premium: flat index for 8 bars - decay alone drags the premium under the
# entry premium once the stop has moved there, so it exits at the entry premium on bar 9.
hi = np.full(12, 84000.0); lo = np.full(12, 84000.0); cl = np.full(12, 84000.0)
got = cs.walk_live_premium(hi, lo, cl, ivs, close_t, 0, dict(tr, t1=84400.0), "next_day", be_bars=8)
p8 = rs.bs(84000, 84000, yrs(close_t[8]), 0.5, True)
check("8 bars without T1, index flat: two hours of decay leave the premium UNDER entry, so moving the "
      "stop to the entry premium puts it above the market -> out at bar 8's own (decayed) premium",
      abs(got[1] - p8) < 1e-9 and got[1] < p0 and got[2] == 8 and got[3] == "stop",
      (round(got[1], 2), round(p0, 2), got[2], got[3]))
# Expiry: a nearest-daily call bought at 17:00 IST settles at 17:30 on its intrinsic value.
idx2 = pd.date_range("2026-10-01 16:45", periods=6, freq="15min", tz="Asia/Kolkata")
ct2 = idx2 + pd.Timedelta(minutes=15)
hi2 = np.array([84000, 84100, 84250, 84300, 84300, 84300], float); lo2 = hi2 - 50; cl2 = hi2 - 20
got = cs.walk_live_premium(hi2, lo2, cl2, np.full(6, 0.5), ct2, 0, dict(tr, t1=None, t2=None, stop=80000.0),
                           "nearest")
check("a nearest-daily contract settling mid-trade is closed at its intrinsic value at 17:30 IST "
      "(close 84,230 - strike 84,000 = 230)", got[3] == "expiry" and abs(got[1] - 230.0) < 1e-9, got)

print()
if fails:
    print(f"CRYPTO STRATEGY STUDY TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("CRYPTO STRATEGY STUDY TEST PASSED")
