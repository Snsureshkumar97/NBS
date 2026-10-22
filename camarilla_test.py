#!/usr/bin/env python3
"""The Camarilla H4/L4 breakout as a live system on Sensex (camarilla.py; the user, 22 Oct 2026: "yes build camarilla
for sensex"): the levels, the one-a-day rule and its window, the reading on a closed candle, the levels a ticket freezes,
the plain exit, Sensex only - and PARITY: the live rule finds the same entries, day and side, as the study that
measured it (indian_strategies_study.run "CAMARILLA") on three years of Sensex candles."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd

import camarilla as cm
import config
import explain
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra if not cond else ''}")
    if not cond:
        fails.append(name)


IST = "Asia/Kolkata"


def day(d, closes, hi_pad=20, lo_pad=20, start="09:15"):
    idx = pd.date_range(f"{d} {start}", periods=len(closes), freq="15min", tz=IST)
    return pd.DataFrame({"Open": closes, "High": [c + hi_pad for c in closes], "Low": [c - lo_pad for c in closes],
                         "Close": closes}, index=idx)


print("1. THE LEVELS")
lv = cm.levels(75000, 74000, 74600)
check("H4 / L4 = close +/- 1.1 x range / 2, H3 / L3 = close +/- 1.1 x range / 4",
      lv == {"h4": 75150.0, "l4": 74050.0, "h3": 74875.0, "l3": 74325.0}, lv)

print("2. THE DAY'S FIRST BREAKOUT, 09:45-14:30, ONE A DAY")
prev = day("2026-10-21", [74500, 74800, 75000 - 20, 74100, 74600])          # H 75000, L 74000 (lows 74080? see below)
prev.loc[prev.index[2], "High"] = 75000
prev.loc[prev.index[3], "Low"] = 74000
pv = cm.levels(75000, 74000, 74600)
first_bar_beyond = day("2026-10-22", [75200, 74900, 75160, 75300, 73900])  # 09:15 candle above H4 is ignored
fb = cm.first_breakout(first_bar_beyond, pv)
check("the day's first candle is never looked at; the 09:45 close (74,900) is inside; the 10:00 close above H4 counts",
      fb == (first_bar_beyond.index[2], 1), fb)
late = day("2026-10-22", [74600] * 21 + [75200])                         # the 14:45 close (candle 14:30) - too late
check("a close beyond H4 after the 14:30 close: no trade", cm.first_breakout(late, pv) is None)
check("at 14:30 exactly it still counts", cm.first_breakout(day("2026-10-22", [74600] * 20 + [75200]), pv) is not None)
both = day("2026-10-22", [74600, 74000, 75300])
check("a put first (73,9xx below L4 at 09:45), then a call close: only the put - one a day",
      cm.first_breakout(both, pv) == (both.index[1], -1))

print("3. THE READING ON THE LAST CLOSED CANDLE")
today = day("2026-10-22", [74600, 74700, 75200, 75000])                 # the last row is the forming candle
df = pd.concat([prev, today])
now = today.index[-1] + pd.Timedelta(seconds=30)
ev = cm.evaluate("SENSEX", df, now=now)
check("the 10:00 close above H4 (75,150): a call, stop at H3 (74,875), fresh within two minutes",
      ev["ready"] and ev["side"] == 1 and ev["stop"] == 74875.0 and ev["fresh"] and ev["values"]["h4"] == 75150.0, ev)
ev2 = cm.evaluate("SENSEX", df, now=today.index[-1] + pd.Timedelta(minutes=5))
check("five minutes after that close: still the reading, but not fresh (no entry)", ev2["side"] == 1 and not ev2["fresh"])
later = pd.concat([prev, day("2026-10-22", [74600, 74700, 75200, 75300, 75400])])
ev3 = cm.evaluate("SENSEX", later, now=later.index[-1] + pd.Timedelta(seconds=10))
check("the next close is above H4 too, but today's breakout was earlier: no trade, said so",
      ev3["side"] == 0 and "Today's one Camarilla trade was the 10:00 close" in (ev3["why"] or ""), ev3.get("why"))
early = pd.concat([prev, day("2026-10-22", [75300, 75400])])
ev4 = cm.evaluate("SENSEX", early, now=early.index[-1] + pd.Timedelta(seconds=10))
check("only the first candle closed (09:30): not used, waits for 09:45", ev4["side"] == 0 and "09:45" in (ev4["why"] or ""))
ev5 = cm.evaluate("SENSEX", df, now=today.index[-1] + pd.Timedelta(minutes=16))
check("the forming candle has not started yet: waiting", not ev5["ready"])
night = cm.evaluate("SENSEX", pd.concat([prev, day("2026-10-22", [74600, 74700, 74800])]),
                    now=pd.Timestamp("2026-10-22 18:00", tz=IST))
check("after the close: the next session's levels said in the wait",
      not night["ready"] and "the next session's levels: H4" in night["why"] and "from 2026-10-22's high, low and close" in night["why"],
      night.get("why"))
check("no earlier session: not ready", not cm.evaluate("SENSEX", today, now=now)["ready"])

print("4. THE LEVELS A TICKET FREEZES")
rec = {"spot": 75210.0, "atm_strike": 75200, "option_chain": {}, "candles": df}
out = cm.apply(dict(rec), ev, "SENSEX")
R = 75210.0 - 74875.0
check("a call at the money, the stop at H3, one target at twice the risk (T1 / T2 / T3 thirds of it)",
      out["option_type"] == "CE" and out["index_stop_loss"] == 74875.0 and out["target_basis"] == "rule"
      and out["index_targets"] == [round(75210 + 2 * R * f / 3, 2) for f in (1, 2, 3)] and out["reach_to_risk"] == 2.0,
      (out.get("index_targets"), out.get("index_stop_loss")))
check("the system and its no-cooldown flag ride with it", out["rule"]["system"] == "camarilla" and out["rule"]["no_cooldown"]
      and out["rule"]["label"] == "Camarilla breakout")
out0 = cm.apply(dict(rec), ev3, "SENSEX")
check("no signal: no trade, in words", out0["option_type"] is None and "Camarilla breakout" in out0["action"])
gone = cm.apply(dict(rec, spot=74800.0), ev, "SENSEX")
check("price already back past the stop: no trade", gone["option_type"] is None and "past its stop" in gone["action"])

print("5. THE PLAIN EXIT, SENSEX ONLY")
check("offered on Sensex only", tickets.system_allowed("camarilla", "SENSEX") and not tickets.system_allowed("camarilla", "NIFTY")
      and not tickets.system_allowed("camarilla", "MIDCPNIFTY") and "camarilla" in config.SYSTEMS)
tr = {"plain_exit": True, "system": "camarilla"}
check("one stop, one target: no step-up, trail or breakeven (rules_exit False), no near-target steps",
      not tickets.rules_exit(tr) and tickets.near_plan(tr) == [])
check("not a default anywhere - the user picks it", "camarilla" not in config.SYSTEM_DEFAULTS.values())

print("6. THE EXPLANATION")
x = explain.explain(out)
check("the verdict and the levels in words", "The day's first 15-minute close above H4: BUY CE" in x["verdict"]
      and x["levels"][0].startswith("STOP 74,875") and "the Camarilla H3; it stays there" in x["levels"][0]
      and "2 x the risk" in x["levels"][1], x)
x0 = explain.explain(out0)
check("no signal: the reason", x0["headline"] == "No trade - Camarilla breakout waiting" and "10:00" in x0["verdict"], x0)

print("7. WIRED IN")
src = open(os.path.join(HERE, "feeds.py")).read()
web = open(os.path.join(HERE, "web_server.py")).read()
check("the feed reads it for an index set to it", 'if system not in ("trend_rider", "tv_rating", "camarilla"):' in src
      and '(camarilla, "Camarilla breakout") if system == "camarilla"' in src)
check("named in Trade settings, badged CM in the Journal", 'camarilla: "Camarilla breakout"' in web
      and 'title="Taken by the Camarilla breakout">CM</span>' in web)

print("8. PARITY WITH THE STUDY THAT MEASURED IT (three years of Sensex)")
import backtest_intraday as bt
import indian_strategies_study as iss
hist = bt.fetch_history("SENSEX", years=3, use_cache=True)
live = {(t + pd.Timedelta(minutes=15), "CE" if s > 0 else "PE") for t, s, _ in cm.signals(hist[["Open", "High", "Low", "Close"]])}
study = {(r["when"], r["side"]) for r in iss.run("SENSEX", "CAMARILLA")}
check(f"every one of the study's {len(study)} trades is a live-rule entry, same moment and side", study <= live,
      sorted(study - live)[:3])
check(f"and the live rule adds none of its own ({len(live)} entries; the study skips only those it could not price)",
      len(live - study) <= 0.02 * len(live), sorted(live - study)[:5])

print()
print("CAMARILLA TEST PASSED" if not fails else f"CAMARILLA TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
