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
    config.MIN_REWARD_RISK_T3 = {"nse_index": 1.0, "crypto": 3.0}   # arbitrary, just > 2.2 - not "the deployed value"
    check("the SAME rec fails once crypto's OWN config value is turned up past it - reads config fresh, not a snapshot",
          crs.reward_gate(0, rec) is False)
finally:
    config.MIN_REWARD_RISK_T3 = _saved
check("MIN_REWARD_RISK_T3 was correctly restored after the try/finally", config.MIN_REWARD_RISK_T3 == _saved)

print("6. THE SWEEP ITSELF NAMES TODAY'S ACTUAL BEFORE/AFTER VALUES")
check("0 (no gate at all - config.py's own convention) is in the sweep", 0 in crs.SWEEP)
check("1.0 (what crypto had before 30 Sep 2026) is in the sweep", 1.0 in crs.SWEEP)
check("DEPLOYED reads config.min_reward_risk_t3('BTC') live, not a number hard-coded into this file - "
      "the whole reason for it, after the deployed value moved twice in one day",
      crs.DEPLOYED == config.min_reward_risk_t3("BTC"), crs.DEPLOYED)
check("today's actual deployed value (whatever config.py ships - 1.0 since 3 Oct 2026) is in the sweep, "
      "so main() can rank it against every alternative",
      crs.DEPLOYED in crs.SWEEP and crs.DEPLOYED == config.MIN_REWARD_RISK_T3["crypto"], crs.DEPLOYED)
check("the sweep is sorted ascending, so the printed table reads as a trend, not shuffled",
      list(crs.SWEEP) == sorted(crs.SWEEP))


def _blank_results():
    """Every swept value present with no result in either period - best_by_period() and
    robust_best() index by SWEEP itself, not by whatever keys a caller happened to fill
    in, so a synthetic results dict for testing them has to cover every value."""
    return {v: {"is": None, "oos": None} for v in crs.SWEEP}


def _stat(total):
    return {"n": 1, "total": total, "pf": 1.0, "dd": 0.0}


print("7. best_by_period(): THE SWEPT VALUE THAT ACTUALLY WON ONE PERIOD")
A, B, C = crs.SWEEP[0], crs.SWEEP[2], crs.SWEEP[-1]
r = _blank_results()
r[A] = {"is": _stat(50), "oos": _stat(5)}
r[B] = {"is": _stat(100), "oos": _stat(9)}     # best in-sample
r[C] = {"is": _stat(30), "oos": _stat(20)}     # best held-out
check("picks the highest in-sample total among the values that have one", crs.best_by_period(r, "is") == B, (r, B))
check("picks the highest held-out total independently of in-sample", crs.best_by_period(r, "oos") == C, (r, C))
check("a period with nothing comparable at all returns None, not a crash", crs.best_by_period(_blank_results(), "is") is None)

print("8. robust_best(): GOOD IN BOTH PERIODS BEATS SPIKING IN ONE")
r = _blank_results()
r[A] = {"is": _stat(100), "oos": _stat(10)}    # tops in-sample, worst held-out - a spike
r[B] = {"is": _stat(90), "oos": _stat(50)}     # the balanced middle candidate
r[C] = {"is": _stat(80), "oos": _stat(90)}     # tops held-out, worst in-sample - the mirror spike
check("the balanced middle value wins, not either value that only excels in ONE period",
      crs.robust_best(r) == B, (crs.robust_best(r), B))

print("9. robust_best(): A VALUE MISSING A WHOLE PERIOD IS EXCLUDED, NOT PICKED BY DEFAULT")
r = _blank_results()
r[A] = {"is": _stat(1_000_000), "oos": None}   # huge in-sample number, but nothing held-out to confirm it
r[B] = {"is": _stat(50), "oos": _stat(50)}     # the only value with both periods present
check("A's huge in-sample number does not win - it has no held-out result to be judged on at all",
      crs.robust_best(r) == B, crs.robust_best(r))
check("robust_best() on a completely empty results dict is None, not a crash", crs.robust_best(_blank_results()) is None)

print("CRYPTO REWARD RISK STUDY TEST PASSED" if not fails else f"CRYPTO REWARD RISK STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
