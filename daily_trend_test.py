#!/usr/bin/env python3
"""Price vs its own N-day daily moving average - a second, genuinely
different timeframe from the 15-minute EMA20/EMA50 trend vote, asked for by
the user on 22 Sep 2026 ("adding moving average... may improve the tool
accuracy"). signal_engine.compute_daily_trend() does the arithmetic;
signal_checks._daily_trend() turns it into a checklist line, non-gating -
same house rule as every other check in that module (see its own docstring:
untested conditions inform the AI desk, they do not gate the rule engine).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import config
import signal_checks as sc
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def daily_df(closes):
    idx = pd.date_range(end="2026-09-22", periods=len(closes), freq="D")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes}, index=idx)


print("1. compute_daily_trend() - THE ARITHMETIC")
check("config carries the period compute_daily_trend defaults to", config.DAILY_MA_PERIOD == 50)
flat = daily_df([100.0] * 60)
check("flat closes: no gap at all - exactly on its own average",
      se.compute_daily_trend(flat)["gap_pct"] == 0.0)
up = daily_df([100.0] * 49 + [110.0])                     # 49 bars at 100, last bar jumps to 110
r = se.compute_daily_trend(up, period=50)
want_ma = (100.0 * 49 + 110.0) / 50                        # 100.2
want_gap = round((110.0 - want_ma) / want_ma * 100.0, 2)   # 9.78 - against the AVERAGE, not the last close
check("price above its own average: a positive gap, the MA and last close both reported",
      r["gap_pct"] > 0 and r["ma"] == round(want_ma, 2) and r["last"] == 110.0 and r["period"] == 50, r)
check("the gap is measured against the moving average, not against the last close itself",
      r["gap_pct"] == want_gap, (r["gap_pct"], want_gap))
down = daily_df([100.0] * 49 + [90.0])
r2 = se.compute_daily_trend(down, period=50)
check("price below its own average: a negative gap", r2["gap_pct"] < 0, r2)
check("exactly enough history: not refused for being short by one",
      se.compute_daily_trend(daily_df([100.0] * 50)) is not None)
check("one bar short of the period: not enough history yet - None, not a guess from a shorter window",
      se.compute_daily_trend(daily_df([100.0] * 49)) is None)
check("no data at all, or an empty frame: None, never an exception",
      se.compute_daily_trend(None) is None and se.compute_daily_trend(daily_df([])) is None)
check("a custom period is honoured, not hardcoded to config's default",
      se.compute_daily_trend(daily_df([100.0] * 20), period=20) is not None
      and se.compute_daily_trend(daily_df([100.0] * 20), period=21) is None)
no_close = daily_df([100.0] * 60).drop(columns=["Close"])
check("a frame without a Close column: None, not a KeyError", se.compute_daily_trend(no_close) is None)
zero = daily_df([0.0] * 60)
check("a zero average (a bad or delisted instrument): None rather than a divide-by-zero gap",
      se.compute_daily_trend(zero) is None)

print("2. signal_checks._daily_trend() - THE CHECKLIST LINE")
item = lambda checks: [i for i in checks["items"] if i["key"] == "daily_trend"][0]
rec = lambda side="CE": {"option_type": side, "index": "NIFTY", "spot": 100.0}
reading = lambda gap, period=50, ma=100.0: {"period": period, "ma": ma, "last": ma * (1 + gap / 100), "gap_pct": gap}

no_dt = sc.evaluate(rec(), None, None, "nse_index", "NIFTY")
check("no reading at all: no_data, not a crash", item(no_dt)["status"] == "no_data" and item(no_dt)["short"] == "no history")

within = sc.evaluate(rec(), None, None, "nse_index", "NIFTY", daily_trend=reading(sc.DAILY_MA_NEUTRAL_PCT / 2))
check("inside the neutral band: neutral, same spirit as RSI's or VWAP's own dead zone",
      item(within)["status"] == "neutral")

above_ce = sc.evaluate(rec("CE"), None, None, "nse_index", "NIFTY", daily_trend=reading(1.0))
above_pe = sc.evaluate(rec("PE"), None, None, "nse_index", "NIFTY", daily_trend=reading(1.0))
check("clearly above the average agrees with a call and is against a put - trading with the bigger trend",
      item(above_ce)["status"] == "agrees" and item(above_pe)["status"] == "against")

below_ce = sc.evaluate(rec("CE"), None, None, "nse_index", "NIFTY", daily_trend=reading(-1.0))
below_pe = sc.evaluate(rec("PE"), None, None, "nse_index", "NIFTY", daily_trend=reading(-1.0))
check("...and the mirror below the average", item(below_ce)["status"] == "against" and item(below_pe)["status"] == "agrees")

right_at = sc.evaluate(rec("CE"), None, None, "nse_index", "NIFTY", daily_trend=reading(sc.DAILY_MA_NEUTRAL_PCT))
check("exactly at the neutral band's edge is still outside it - the check is a strict less-than",
      item(right_at)["status"] != "neutral")

r3 = reading(2.5, period=50, ma=23400.0)
i3 = item(sc.evaluate(rec("CE"), None, None, "nse_index", "NIFTY", daily_trend=r3))
check("the label names the period, the sentence gives the direction, the level and the gap",
      i3["label"] == "50-day MA" and "above" in i3["detail"] and "23,400.00" in i3["detail"] and "2.50%" in i3["detail"], i3)
check("the short reading on the bar is the signed gap against the period",
      i3["short"] == "+2.50% vs 50d", i3["short"])
i4 = item(sc.evaluate(rec("PE"), None, None, "nse_index", "NIFTY", daily_trend=reading(-3.2)))
check("a negative gap reads 'below', signed correctly in the short form too",
      "below" in i4["detail"] and i4["short"] == "-3.20% vs 50d", i4)

print("3. EVERY MARKET GETS IT - UNLIKE FII/DII, THIS IS EACH INSTRUMENT'S OWN PRICE, NOT INDIA-ONLY")
btc = sc.evaluate({"option_type": "CE", "index": "BTC", "spot": 85000.0}, None, None, "crypto", "BTC",
                  daily_trend=reading(1.0, ma=84000.0))
check("Bitcoin gets a daily_trend line too, and it can agree just like an index's",
      item(btc)["status"] == "agrees")
check("...unlike institutions, which crypto never gets",
      "institutions" not in [i["key"] for i in btc["items"]])

print("4. IT NEVER DECIDES ANYTHING - THE SAME INVARIANT EVERY OTHER CHECK HONOURS")
tk = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickets.py")).read()
se_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_engine.py")).read()
check("neither the ticket engine nor the signal engine reads the checklist to decide anything",
      "signal_checks" not in tk and "signal_checks" not in se_src)
check("...and compute_daily_trend's own return value carries no agree/against judgment, only the numbers",
      set(se.compute_daily_trend(daily_df([100.0] * 49 + [110.0]))) == {"period", "ma", "last", "gap_pct"})

print("DAILY TREND TEST PASSED" if not fails else f"DAILY TREND TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
