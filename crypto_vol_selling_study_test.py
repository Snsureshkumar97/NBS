#!/usr/bin/env python3
"""Hand-traced checks for crypto_vol_selling_study.sell_day() - a short straddle's
credit, settlement at intrinsic, costs, and the 2x-credit stop."""
import os, sys, tempfile
os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import crypto_vol_selling_study as vs
import crypto_strategy_study as cs
import regime_study as rs

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

T = lambda s: pd.Timestamp(s, tz="Asia/Kolkata")
t0, exp = T("2026-10-01 17:30"), T("2026-10-02 17:30")
times = pd.date_range(t0 + pd.Timedelta(minutes=15), exp, freq="15min")
n = len(times)
v = 0.45
c0 = rs.bs(84000, 84000, 1 / 365, v, True); p0 = rs.bs(84000, 84000, 1 / 365, v, False)
credit = c0 + p0
cost_in = cs.option_fee(c0, 84000) + cs.option_fee(p0, 84000) + 0.01 * credit

print("1. A FLAT DAY: BOTH LEGS EXPIRE WORTHLESS - KEEP THE CREDIT LESS ENTRY COSTS")
flat = np.full(n, 84000.0)
got = vs.sell_day(flat, flat, flat, times, 84000.0, t0, exp, v, np.full(n, v))
check("net = credit - both legs' fees - half the spread", abs(got[0] - (credit - cost_in)) < 1e-9 and got[2] == "settled",
      (round(got[0], 2), round(credit - cost_in, 2)))
check("the credit is the BS call + put at 24h", abs(got[1] - credit) < 1e-9, round(credit, 2))

print("2. A 3,000-POINT RALLY: THE CALL SETTLES 3,000 IN THE MONEY")
up = np.linspace(84000, 87000, n)
got = vs.sell_day(up, up, up, times, 84000.0, t0, exp, v, np.full(n, v))
want = credit - 3000 - cost_in - cs.option_fee(3000, 87000)
check("net = credit - 3,000 intrinsic - entry costs - the settlement fee on the ITM call", abs(got[0] - want) < 1e-6,
      (round(got[0], 2), round(want, 2)))

print("3. THE 2x-CREDIT STOP CAPS A RUNAWAY DAY")
spike = np.full(n, 84000.0); spike_hi = spike.copy(); spike_hi[10:] = 92000.0
got = vs.sell_day(spike_hi, spike, spike, times, 84000.0, t0, exp, v, np.full(n, v), stop_mult=2.0)
buy = 2 * credit
want = credit - buy - cost_in - (cs.option_fee(buy / 2, 84000) * 2 + 0.01 * buy)
check("bought back at 2x credit on the bar the high reaches 92,000 -> loss about one credit plus costs",
      got[2] == "stopped" and abs(got[0] - want) < 1e-6, (got[2], round(got[0], 2), round(want, 2)))
got2 = vs.sell_day(spike_hi, spike, spike, times, 84000.0, t0, exp, v, np.full(n, v))
check("...while the same day WITHOUT the stop loses the full intrinsic (here it settles flat - the spike reversed)",
      got2[2] == "settled")

print("4. THE SELLING VOL NEVER USES A FUTURE MEASUREMENT")
idx = pd.date_range("2026-01-01", periods=30 * 24, freq="1h", tz="Asia/Kolkata")
dv = pd.Series(0.5, index=idx)
samp = pd.DataFrame({"ratio": [0.8, 0.8, 0.8, 2.0, 2.0, 2.0, 2.0]},
                    index=pd.DatetimeIndex([idx[24 * d] for d in (1, 3, 5, 7, 9, 11, 13)]))
sv = vs.sell_vol_series(dv, samp)
check("before 3 measurements exist the day is skipped (NaN)", np.isnan(sv.loc[idx[24 * 4]]))
check("day 6 has only two measurements BEFORE it (days 1, 3) - still skipped", np.isnan(sv.loc[idx[24 * 6]]))
check("from the day-7 measurement on, three before-or-at are known: 0.5 x median 0.8 = 0.40 on day 8",
      abs(sv.loc[idx[24 * 8]] - 0.40) < 1e-12, sv.loc[idx[24 * 8]])
# At the day-13 measurement: the 6 BEFORE it (0.8 x3, 2.0 x3) have median 1.4 -> 0.70. If that day's own
# 2.0 were (wrongly) included, the median of 7 would be 2.0 -> 1.00.
check("the day-13 measurement is NOT used at day 13 itself - only the 6 before it (median 1.4 -> 0.70)",
      abs(sv.loc[idx[24 * 13]] - 0.70) < 1e-12, sv.loc[idx[24 * 13]])

print()
if fails:
    print(f"VOL SELLING STUDY TEST FAILED - {len(fails)}: " + "; ".join(fails)); sys.exit(1)
print("VOL SELLING STUDY TEST PASSED")
