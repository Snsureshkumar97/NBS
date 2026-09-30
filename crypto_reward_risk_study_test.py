#!/usr/bin/env python3
"""crypto_reward_risk_study.py's own module-level helpers, hand-traced - the same
discipline every other *_study.py file in this project is held to (pull testable
logic out of main()/closures, test it directly, don't just eyeball the printed
report). Fakes only - no network, no real history fetch.
"""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import config
import crypto_reward_risk_study as crs
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. IMPORTING THE MODULE ITSELF PROVES _reward_hold IS STILL STATELESS")
# _assert_reward_hold_stateless() already ran at import time, above - reaching this
# line at all (no AttributeError) is the check. Re-run it explicitly too, so a
# future test run that only imports this file lazily still exercises it.
crs._assert_reward_hold_stateless()
check("re-running the statelessness assertion directly does not raise", True)

print("2. price_bitcoin_trade(): POINTS, SIGNED FOR THE SIDE")
check("a CE: exit above entry is a positive number of points",
      crs.price_bitcoin_trade({"entry": 100.0, "exit": 108.0, "side": "CE"}) == 8.0)
check("a CE: exit below entry is negative", crs.price_bitcoin_trade({"entry": 100.0, "exit": 92.0, "side": "CE"}) == -8.0)
check("a PE: exit BELOW entry is positive (a put profits on the way down)",
      crs.price_bitcoin_trade({"entry": 100.0, "exit": 92.0, "side": "PE"}) == 8.0)
check("a PE: exit above entry is negative", crs.price_bitcoin_trade({"entry": 100.0, "exit": 108.0, "side": "PE"}) == -8.0)

print("3. bitcoin_stats(): TOTAL, PROFIT FACTOR, DRAWDOWN")
s = crs.bitcoin_stats([100, -40, 60, -20])
check("n, total", s["n"] == 4 and s["total"] == 100.0, s)
check("profit factor is wins / -losses", s["pf"] == (160 / 60), s["pf"])
check("drawdown is the worst peak-to-trough walked in trade order (100 -> 60 -> 120 -> 100: dd 40, not 60)",
      s["dd"] == 40.0, s)
check("bitcoin_stats([]) is None, not a crash", crs.bitcoin_stats([]) is None)
check("no losses at all: profit factor is +inf, not a division error", crs.bitcoin_stats([10, 20])["pf"] == float("inf"))

print("4. split_stats(): BUCKETED BY THE SAME SPLIT pro_study.py USES")
before = crs.SPLIT - pd.Timedelta(days=1)
after = crs.SPLIT + pd.Timedelta(days=1)
trades = [
    {"when": before, "entry": 100.0, "exit": 110.0, "side": "CE"},   # in-sample, +10
    {"when": before, "entry": 100.0, "exit": 90.0, "side": "CE"},    # in-sample, -10
    {"when": after, "entry": 100.0, "exit": 115.0, "side": "CE"},    # held-out, +15
]
ss = crs.split_stats(trades)
check("in-sample gets the two trades before SPLIT", ss["is"]["n"] == 2 and ss["is"]["total"] == 0.0, ss["is"])
check("held-out gets the one trade on/after SPLIT", ss["oos"]["n"] == 1 and ss["oos"]["total"] == 15.0, ss["oos"])
check("a trade exactly AT the split lands in held-out, not in-sample - '< SPLIT' is a strict cut",
      crs.split_stats([{"when": crs.SPLIT, "entry": 100.0, "exit": 100.0, "side": "CE"}])["oos"]["n"] == 1)

print("5. reward_gate(): DELEGATES TO TODAY'S REAL GATE, READS CONFIG FRESH")
_saved = config.MIN_REWARD_RISK_T3
try:
    # T3 22 away from spot on a 10-point stop: 2.2 to 1.
    rec = {"index": "BTC", "spot": 100.0, "risk_points": 10.0, "index_targets": [None, None, 122.0]}
    config.MIN_REWARD_RISK_T3 = {"nse_index": 1.0, "crypto": 1.0}
    check("2.2 to 1 passes need=1.0", crs.reward_gate(0, rec) is True)
    config.MIN_REWARD_RISK_T3 = {"nse_index": 1.0, "crypto": 2.5}
    check("the SAME rec fails once crypto's OWN config value is turned up past it - reads config fresh, not a snapshot",
          crs.reward_gate(0, rec) is False)
finally:
    config.MIN_REWARD_RISK_T3 = _saved
check("MIN_REWARD_RISK_T3 was correctly restored after the try/finally", config.MIN_REWARD_RISK_T3 == _saved)

print("6. THE SWEEP ITSELF NAMES TODAY'S ACTUAL BEFORE/AFTER VALUES")
check("1.0 (what crypto had before 30 Sep 2026) is in the sweep", 1.0 in crs.SWEEP)
check("2.5 (today's deployed value) is in the sweep", 2.5 in crs.SWEEP)
check("the sweep is sorted ascending, so the printed table reads as a trend, not shuffled",
      list(crs.SWEEP) == sorted(crs.SWEEP))

print("CRYPTO REWARD RISK STUDY TEST PASSED" if not fails else f"CRYPTO REWARD RISK STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
