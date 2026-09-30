#!/usr/bin/env python3
"""MIN_REWARD_RISK_T3 (the "ticket gate": room to run / stop distance must
clear this before a trade is issued) went per-market on 30 Sep 2026, at the
user's request: "change crypto risk reward from 1:1 to 1:2.50." Raised to 2.5
first, then to 2.0 the same day after crypto_reward_risk_study.py's sweep
against BTC's own history found 2.5 ranked near the bottom of everything
swept on raw profit ("change it to 2.0 and deploy"). The Indian indices stay
at 1.0 throughout - only crypto's value has ever moved. Same shape as
config.adx_dx_smoothing()/ADX_DX_SMOOTHING (adx_speed_test.py).

Same day, right after: user - "when does the risk reward says it needs 1x
when we chnged to 2x" -> "btc" -> "signal card". This gate had nowhere to be
SEEN except inside a LOW REWARD hold message - the "1x" the user was reading
was reach_to_risk, a different, never-changed gate shown on the same card.
tickets._reward_hold() was refactored so its own ratio/need pair is now a
module-level function, reward_risk_t3(), reused by feeds._public() to put a
"Ticket gate" tile on the Signal card next to "Reward : risk" - the same
number the gate itself decides on, not a second copy of it. "yes add it."

Fakes only - no Zerodha/Delta call and no socket.
"""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import feeds
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
    check(f"{k} (crypto market) is 2.0, the user's value", config.min_reward_risk_t3(k) == 2.0, config.min_reward_risk_t3(k))
check("no instrument named means the default market (NSE) - the desktop app and main.py's single-index runs",
      config.min_reward_risk_t3() == 1.0, config.min_reward_risk_t3())
check("an unknown instrument falls back to the default market's value, not a crash",
      config.min_reward_risk_t3("NOT_A_REAL_INSTRUMENT") == 1.0)

print("2. THE LIVE GATE ITSELF - THE SAME rec, ONLY THE INSTRUMENT DIFFERS")
book = object.__new__(tickets.TicketBook)
def rec(index, spot=100.0, risk=10.0, t3=122.0):
    # T3 is 22 away from spot on a 10-point stop: 2.2 to 1 - passes the
    # Indian indices' 1.0 bar AND crypto's 2.0 bar. Used as the baseline;
    # each check below moves t3 to the boundary that actually matters.
    return {"index": index, "spot": spot, "risk_points": risk, "index_targets": [None, None, t3]}

check("NIFTY: 1.15 to 1 passes the Indian indices' 1.0 gate",
      book._reward_hold("NIFTY", rec("NIFTY", t3=111.5)) is None)
check("BTC: the SAME 1.15 to 1 is held on crypto's 2.0 gate - LOW REWARD",
      (book._reward_hold("BTC", rec("BTC", t3=111.5)) or (None,))[0] == "low_rr")
check("BTC: 2.6 to 1 clears crypto's own 2.0 bar", book._reward_hold("BTC", rec("BTC", t3=126.0)) is None)
check("BTC: exactly 2.0 to 1 also clears - the gate is >=, not a strict >",
      book._reward_hold("BTC", rec("BTC", t3=120.0)) is None)
hold = book._reward_hold("BTC", rec("BTC", t3=111.5))
check("the LOW REWARD reason names the actual ratio measured, not the instrument's raw points",
      hold is not None and "1.15 to 1" in hold[2], hold)

print("2b. reward_risk_t3() ITSELF - THE ONE NUMBER BOTH _reward_hold() AND THE SIGNAL CARD NOW SHARE")
check("returns the exact ratio and threshold _reward_hold() gates on",
      tickets.reward_risk_t3("BTC", rec("BTC", t3=126.0)) == (2.6, 2.0),
      tickets.reward_risk_t3("BTC", rec("BTC", t3=126.0)))
check("the SAME rec on an Indian index returns ITS OWN threshold, not crypto's",
      tickets.reward_risk_t3("NIFTY", rec("NIFTY", t3=126.0)) == (2.6, 1.0))
check("no spot/risk/T3 yet (a NEUTRAL bias, no targets): (None, None), not a crash",
      tickets.reward_risk_t3("BTC", {"index": "BTC", "spot": 100.0, "risk_points": 10.0, "index_targets": [None, None, None]}) == (None, None))
old_min = config.MIN_REWARD_RISK_T3
try:
    config.MIN_REWARD_RISK_T3 = {"nse_index": 1.0, "crypto": 0}
    check("the gate switched off (0) for this market: (None, None), same as _reward_hold()'s own 'not need' early-out",
          tickets.reward_risk_t3("BTC", rec("BTC")) == (None, None))
finally:
    config.MIN_REWARD_RISK_T3 = old_min
rr_unrounded = tickets.reward_risk_t3("BTC", rec("BTC", spot=100.0, risk=3.0, t3=107.0))[0]
check("the ratio is NOT rounded to 2dp - _reward_hold()'s own rr < need comparison needs this exact value "
      "(7/3 = 2.3333...repeating, not truncated to 2.33)",
      abs(rr_unrounded - 7 / 3) < 1e-12 and abs(rr_unrounded - 2.33) > 1e-6, rr_unrounded)

print("2c. feeds._public() PUTS THE SAME PAIR ON THE SIGNAL CARD'S OWN PAYLOAD")
pub = feeds._public(rec("BTC", t3=126.0), "BTC")
check("ticket_gate_ratio is exactly what reward_risk_t3() returns, not a re-derived copy",
      pub["ticket_gate_ratio"] == 2.6, pub["ticket_gate_ratio"])
check("ticket_gate_need is crypto's own threshold", pub["ticket_gate_need"] == 2.0, pub["ticket_gate_need"])
pub_nifty = feeds._public(rec("NIFTY", t3=126.0), "NIFTY")
check("the same call for an Indian index carries ITS OWN threshold on the payload",
      pub_nifty["ticket_gate_need"] == 1.0, pub_nifty["ticket_gate_need"])
check("no rec at all: _public() itself returns None (its own existing behaviour, unaffected)",
      feeds._public(None, "BTC") is None)

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
check("crypto's own, different figure is on the page too", "2.0</code> on crypto" in html, "2.0</code> on crypto" in html)
check("no stray dict repr leaked onto the page (the bug this would have been if _cfg() had kept reading it as a flat value)",
      "nse_index" not in html and "{'nse_index'" not in html and "MIN_REWARD_RISK_T3" not in html)

print("5. TODAY'S SPLIT VALUES, EXPLICITLY - CATCHES A SILENT REVERT TO ONE SHARED NUMBER")
check("nse_index is exactly 1.0", config.MIN_REWARD_RISK_T3["nse_index"] == 1.0)
check("crypto is exactly 2.0, the value asked for", config.MIN_REWARD_RISK_T3["crypto"] == 2.0)

print("6. THE SIGNAL CARD: A VISIBLE 'TICKET GATE' TILE, NEXT TO 'REWARD : RISK', NOT INSTEAD OF IT")
SRC = open("web_server.py").read()
start = SRC.index('$("tiles").innerHTML =')
tiles_block = SRC[start:SRC.index(";", SRC.index('tile("Ticket gate"', start)) + 1]
check("'Reward : risk' is still there, unchanged", 'tile("Reward : risk"' in tiles_block)
check("a new 'Ticket gate' tile reads the two new fields, not a re-derived number",
      'tile("Ticket gate"' in tiles_block and "r.ticket_gate_ratio" in tiles_block and "r.ticket_gate_need" in tiles_block)
check("it shows the actual threshold in its sub-text, not a hard-coded number",
      '"needs "+r.ticket_gate_need' in tiles_block)
check("green when the ratio clears ITS OWN threshold, not some fixed number - unlike the Reward : risk tile next to "
      "it, which does use a fixed >=2 for its own colour",
      "r.ticket_gate_ratio>=r.ticket_gate_need" in tiles_block.replace(" ", ""))
check("a missing ratio (no signal yet) shows an em dash, not null or NaN on the page",
      'r.ticket_gate_ratio==null?"—"' in tiles_block.replace(" ", ""))

print("7. THE RISK-AND-REWARD TABLE'S OWN NOTE NAMES BOTH GATES NOW, NOT JUST ONE")
note = SRC[SRC.index("function rrBox("):SRC.index("function rrBox(") + 6000]
check("the note distinguishes 'x risk' (this table) from BOTH Signal-card tiles, not just the one it used to name",
      "Reward : risk tile" in note and "Ticket gate tile" in note)
check("it no longer claims the Reward : risk tile alone is 'the check that decides whether a trade is issued at "
      "all' - there are now two, and the note says so",
      "different checks decide whether a trade is issued at all" in note)

print("MIN REWARD RISK T3 TEST PASSED" if not fails else f"MIN REWARD RISK T3 TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
