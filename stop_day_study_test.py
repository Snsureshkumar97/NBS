#!/usr/bin/env python3
"""stop_day_study's own pieces on hand-made trades: the cross-index cap (A), the opening-move gate (B), the rising-ADX
gate (C) - so the 3-year numbers measure the rule they claim to."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

import config
import stop_day_study as sds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

T = lambda hm: pd.Timestamp(f"2026-10-07 {hm}", tz="Asia/Kolkata")
def tr(index, side, start, end, via="stop", net=-100.0):
    return {"index": index, "side": side, "when": T(start), "exit_time": T(end), "closed_via": via, "exit_adx": 15.0,
            "net": net}

print("A. ONE BET, NOT THREE")
day = [tr("NIFTY", "PE", "09:30", "10:30"), tr("SENSEX", "PE", "09:45", "10:15"), tr("BANKNIFTY", "PE", "09:45", "10:00")]
pick = lambda rows: [(r["index"], r["when"].strftime("%H:%M")) for r in rows]
check("7 Oct's shape, no cap: all three puts", len(sds.joint(day, None)) == 3)
check("cap 1: only the first put", pick(sds.joint(day, 1)) == [("NIFTY", "09:30")], pick(sds.joint(day, 1)))
check("cap 2: the first two", len(sds.joint(day, 2)) == 2, pick(sds.joint(day, 2)))
mixed = [tr("NIFTY", "PE", "09:30", "10:30"), tr("SENSEX", "CE", "09:45", "10:15")]
check("the OTHER direction is not capped (a call while a put is open)", len(sds.joint(mixed, 1)) == 2)
later = [tr("NIFTY", "PE", "09:30", "10:00"), tr("SENSEX", "PE", "10:00", "10:30")]
check("a trade after the first one CLOSED is allowed", len(sds.joint(later, 1)) == 2)
same_index = [tr("NIFTY", "PE", "09:30", "10:30"), tr("NIFTY", "PE", "09:45", "11:00")]
check("one position per index still applies", len(sds.joint(same_index, None)) == 1)
cool = [tr("NIFTY", "PE", "09:30", "10:00"), tr("NIFTY", "PE", "10:05", "11:00")]
check(f"...and the {config.REENTRY_COOLDOWN_MIN}-minute cooldown after a stop", len(sds.joint(cool, None)) == 1)
waived = [tr("NIFTY", "PE", "09:30", "10:00", via="target"), tr("NIFTY", "PE", "10:05", "11:00")]
waived[0]["exit_adx"] = 30.0
check("...waived after a target with ADX still strong (as live)", len(sds.joint(waived, None)) == 2)
check("joint(None) equals the per-index walk", pick(sds.joint(day + cool, None)) == pick(sds.per_index(day + cool)))

print("B. DON'T CHASE THE OPENING MOVE")
idx = pd.DatetimeIndex([T("09:15"), T("09:30"), T("09:45"), T("10:00"), T("10:15"), T("10:30")])
df = pd.DataFrame({"Close": [100.0] * 6}, index=idx)
move = {dt.date(2026, 10, 7): -0.0067}                  # 7 Oct: the first candle closed 0.67% under 6 Oct's close
g = sds.chase_gate(lambda i, rec: True, df, move, 0.005, dt.time(10, 15))
check("a put off the 09:15 candle on a 0.67% gap down: blocked", g(0, {"option_type": "PE"}) is False)
check("...off the 10:00 candle: blocked (before the 10:15 cutoff)", g(3, {"option_type": "PE"}) is False)
check("...off the 10:15 candle: allowed", g(4, {"option_type": "PE"}) is True)
check("a CALL that morning (against the move): allowed", g(0, {"option_type": "CE"}) is True)
small = sds.chase_gate(lambda i, rec: True, df, {dt.date(2026, 10, 7): -0.003}, 0.005, dt.time(10, 15))
check("a 0.3% move under a 0.5% threshold: allowed", small(0, {"option_type": "PE"}) is True)
up = sds.chase_gate(lambda i, rec: True, df, {dt.date(2026, 10, 7): +0.008}, 0.005, dt.time(10, 15))
check("5 Oct's mirror (gap UP, a call): blocked", up(1, {"option_type": "CE"}) is False and up(1, {"option_type": "PE"}) is True)
base_no = sds.chase_gate(lambda i, rec: False, df, {}, 0.005, dt.time(10, 15))
check("the live checks still apply on a quiet morning", base_no(0, {"option_type": "PE"}) is False)
two = pd.DataFrame({"Close": [100.0, 101.0, 99.0, 98.0]},
                   index=pd.DatetimeIndex([T("15:15").replace(day=6), T("09:15"), T("09:30"), T("09:45")]))
two.index = pd.DatetimeIndex([pd.Timestamp("2026-10-06 15:15", tz="Asia/Kolkata"), T("09:15"), T("09:30"), T("09:45")])
m = sds.opening_move(two)
check("opening move = the first candle's close vs yesterday's last close", abs(m[dt.date(2026, 10, 7)] - 0.01) < 1e-12, m)

print("C. ADX RISING")
pre = {"adx": pd.Series([20.0, 25.0, 24.0, 24.0])}
r = sds.rising_gate(lambda i, rec: True, pre)
check("25 after 20: allowed; 24 after 25: blocked; flat: blocked; the first bar: blocked",
      [r(i, {}) for i in range(4)] == [False, True, False, False], [r(i, {}) for i in range(4)])

print()
print("STOP DAY STUDY TEST PASSED" if not fails else f"STOP DAY STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
