#!/usr/bin/env python3
"""Trend-day room in compute_reachability: only on a day that has used its normal
range, only in the direction price is pressing, and never by looking ahead."""
import datetime as dt

import pandas as pd

import config
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 9, 15, 11, 0, tzinfo=IST)
NO_CHAIN = se.compute_option_chain_signal(None)
ONE = pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0]},
                   index=pd.DatetimeIndex([pd.Timestamp("2026-09-15 10:45", tz="Asia/Kolkata")]))
was = getattr(config, "TREND_DAY_ROOM", False)


def reach(spot, typical, used, ext, flag, with_stats=True, df=ONE):
    config.TREND_DAY_ROOM = flag
    return se.compute_reachability(spot, NO_CHAIN, df, NOW, adx=10.0,
                                   range_stats=(typical, used) if with_stats else None,
                                   index_key="NIFTY", day_extremes=ext)


try:
    base = reach(23380, 200, 250, (23600, 23350), False)
    t = base["typical_daily_range"]
    print("1. SWITCHED OFF")
    check("a day past its normal range has no room left, both ways", base["reach_up"] is None and base["reach_down"] is None, (base["reach_up"], base["reach_down"]))

    print("2. SWITCHED ON")
    r = reach(23380, 200, 250, (23600, 23350), True)
    check("near the day's low: one more normal day's room DOWN", r["reach_down"] == t and "trend day" in (r["cap_down"] or ""), (r["reach_down"], r["cap_down"]))
    check("...and nothing added UP", r["reach_up"] is None and r.get("trend_day") == "down", r["reach_up"])
    r = reach(23580, 200, 250, (23600, 23350), True)
    check("near the day's high: the room goes UP only", r["reach_up"] == t and r["reach_down"] is None and r.get("trend_day") == "up", (r["reach_up"], r["reach_down"]))
    r = reach(23475, 200, 250, (23600, 23350), True)
    check("in the middle of the day's range: no trend-day room", r["reach_up"] is None and r["reach_down"] is None and "trend_day" not in r)
    r = reach(23380, 200, 150, (23500, 23350), True)
    check("a day that has not used its normal range is unchanged", r["reach_down"] == round(t - 150, 2) and "trend_day" not in r, r["reach_down"])
    edge = 23350 + 0.20 * 250
    check("the 20% band is inclusive at its edge", reach(edge, 200, 250, (23600, 23350), True).get("trend_day") == "down")
    check("just outside it is not", "trend_day" not in reach(edge + 1, 200, 250, (23600, 23350), True))

    print("3. NO LOOK-AHEAD")
    r = reach(23380, 200, 250, None, True)
    check("stats handed in without the day's extremes: nothing inferred from the frame", r["reach_down"] is None and "trend_day" not in r)
    live = pd.DataFrame({"Open": [23590.0, 23400.0], "High": [23600.0, 23410.0], "Low": [23500.0, 23350.0],
                         "Close": [23510.0, 23380.0]},
                        index=pd.DatetimeIndex([pd.Timestamp("2026-09-15 09:15", tz="Asia/Kolkata"),
                                                pd.Timestamp("2026-09-15 10:45", tz="Asia/Kolkata")]))
    check("live (no stats handed in): today's own candles supply the extremes", se._today_extremes(live) == (23600.0, 23350.0))
finally:
    config.TREND_DAY_ROOM = was

print("TREND ROOM TEST PASSED" if not fails else f"TREND ROOM TEST FAILED: {fails}")
