#!/usr/bin/env python3
"""MIN_REWARD_RISK_T3 (the "ticket gate": room to run / stop distance must
clear this before a trade is issued) went per-market on 30 Sep 2026, at the
user's request: "change crypto risk reward from 1:1 to 1:2.50." The Indian
indices stay at 1.0 - only crypto moves, to 2.5. Same shape as
config.adx_dx_smoothing()/ADX_DX_SMOOTHING (adx_speed_test.py).

Fakes only - no Zerodha/Delta call and no socket.
"""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE LOOKUP ITSELF")
for k in ("NIFTY", "BANKNIFTY", "SENSEX"):
    check(f"{k} (Indian index) is unchanged at 1.0", config.min_reward_risk_t3(k) == 1.0, config.min_reward_risk_t3(k))
for k in ("BTC", "GOLD"):
    check(f"{k} (crypto market) is 2.5, the user's new value", config.min_reward_risk_t3(k) == 2.5, config.min_reward_risk_t3(k))
check("no instrument named means the default market (NSE) - the desktop app and main.py's single-index runs",
      config.min_reward_risk_t3() == 1.0, config.min_reward_risk_t3())
check("an unknown instrument falls back to the default market's value, not a crash",
      config.min_reward_risk_t3("NOT_A_REAL_INSTRUMENT") == 1.0)

print("2. THE LIVE GATE ITSELF - THE SAME rec, ONLY THE INSTRUMENT DIFFERS")
book = object.__new__(tickets.TicketBook)
def rec(index, spot=100.0, risk=10.0, t3=122.0):
    # T3 is 22 away from spot on a 10-point stop: 2.2 to 1 - passes the
    # Indian indices' 1.0 bar AND crypto's 2.5 bar. Used as the baseline;
    # each check below moves t3 to the boundary that actually matters.
    return {"index": index, "spot": spot, "risk_points": risk, "index_targets": [None, None, t3]}

check("NIFTY: 1.15 to 1 passes the Indian indices' 1.0 gate",
      book._reward_hold("NIFTY", rec("NIFTY", t3=111.5)) is None)
check("BTC: the SAME 1.15 to 1 is held on crypto's 2.5 gate - LOW REWARD",
      (book._reward_hold("BTC", rec("BTC", t3=111.5)) or (None,))[0] == "low_rr")
check("BTC: 2.6 to 1 clears crypto's own 2.5 bar", book._reward_hold("BTC", rec("BTC", t3=126.0)) is None)
check("BTC: exactly 2.5 to 1 also clears - the gate is >=, not a strict >",
      book._reward_hold("BTC", rec("BTC", t3=125.0)) is None)
hold = book._reward_hold("BTC", rec("BTC", t3=111.5))
check("the LOW REWARD reason names the actual ratio measured, not the instrument's raw points",
      hold is not None and "1.15 to 1" in hold[2], hold)

print("3. THE AI DESK IS TOLD ITS OWN INSTRUMENT'S BAR, NOT A SHARED GLOBAL ONE")
import market_bot
src = open("market_bot.py").read()
check("market_bot.py reads the per-instrument helper, the same way it already does for adx_dx_smoothing(index)",
      '"min_reward_to_risk_T3": config.min_reward_risk_t3(index)' in src)
check("no caller left reading the old flat global directly",
      'getattr(config, "MIN_REWARD_RISK_T3"' not in src)

print("4. THE MARKETING SITE RENDERS BOTH VALUES, NOT A DICT'S repr()")
import nbs_site
html = nbs_site.how_page()
check("the Indian indices' own figure is on the page", "1.0</code> on the Indian indices" in html, "1.0</code> on the Indian indices" in html)
check("crypto's own, different figure is on the page too", "2.5</code> on crypto" in html, "2.5</code> on crypto" in html)
check("no stray dict repr leaked onto the page (the bug this would have been if _cfg() had kept reading it as a flat value)",
      "nse_index" not in html and "{'nse_index'" not in html and "MIN_REWARD_RISK_T3" not in html)

print("5. TODAY'S SPLIT VALUES, EXPLICITLY - CATCHES A SILENT REVERT TO ONE SHARED NUMBER")
check("nse_index is exactly 1.0", config.MIN_REWARD_RISK_T3["nse_index"] == 1.0)
check("crypto is exactly 2.5, the value asked for", config.MIN_REWARD_RISK_T3["crypto"] == 2.5)

print("MIN REWARD RISK T3 TEST PASSED" if not fails else f"MIN REWARD RISK T3 TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
