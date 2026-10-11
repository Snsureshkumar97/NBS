#!/usr/bin/env python3
"""Bollinger Bands as a vote (bollinger_vote_study.py, 11 Oct 2026) - indicators.bollinger(), signal_engine's bb_score
and bb_break_score, the two names config.VOTE_OVERRIDES can add, and the backtest's fast path. Hand-traced.

Live must be untouched: the Indian market's VOTE_OVERRIDES is {} and neither Bollinger vote counts anywhere unless a
market adds it. A vote added there must actually reach build_recommendation() - and the backtest's own tech dict must
carry it too, or a study of it silently equals the baseline (the trap the +DI/-DI study nearly fell into)."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import math

import pandas as pd

import config
import indicators as ind
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def bars(closes):
    idx = pd.date_range("2026-10-01 09:15", periods=len(closes), freq="15min")
    return pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes], "Low": [c - 1 for c in closes],
                         "Close": closes, "Volume": [100] * len(closes)}, index=idx)


print("1. indicators.bollinger() - HAND-TRACED")
mid, up, lo = ind.bollinger(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 3, 2.0)
sd = math.sqrt(2 / 3)                                  # population deviation of 1, 2, 3 (and of 3, 4, 5)
check("the middle is the 3-candle average (2, then 4)", mid.iloc[2] == 2.0 and mid.iloc[4] == 4.0, list(mid))
check("the bands are 2 POPULATION deviations either side (ddof=0, as TradingView draws them)",
      abs(up.iloc[2] - (2 + 2 * sd)) < 1e-12 and abs(lo.iloc[4] - (4 - 2 * sd)) < 1e-12, (up.iloc[2], lo.iloc[4]))
check("nothing before a full window", all(math.isnan(v) for v in (mid.iloc[0], up.iloc[1], lo.iloc[1])))

print("2. THE TWO VOTES, FROM THE REAL compute_technical_signal()")
base = [100.0 + (i % 2) * 0.5 for i in range(80)]      # flat, a tiny saw-tooth: the bands sit tight round 100.25
t = se.compute_technical_signal(bars(base[:-1] + [100.3]))
check("just above the middle, inside the bands: middle-band vote +1, break vote abstains",
      t["bb_score"] == 1 and t["bb_break_score"] == 0, (t["bb_score"], t["bb_break_score"]))
t = se.compute_technical_signal(bars(base[:-1] + [103.0]))
check("a close above the upper band: both +1", t["bb_score"] == 1 and t["bb_break_score"] == 1,
      (t["bb_score"], t["bb_break_score"]))
t = se.compute_technical_signal(bars(base[:-1] + [97.0]))
check("a close below the lower band: both -1", t["bb_score"] == -1 and t["bb_break_score"] == -1,
      (t["bb_score"], t["bb_break_score"]))
t = se.compute_technical_signal(bars(base[:-1] + [100.2]))
check("just below the middle, inside: -1 and abstain", t["bb_score"] == -1 and t["bb_break_score"] == 0,
      (t["bb_score"], t["bb_break_score"]))
check("neither is counted in the four-vote score", t["max_score"] == 4)

print("3. THE SWITCH: OFF LIVE, AND REAL WHEN A MARKET ADDS IT")
check("the Indian market adds no vote (live unchanged)", config.VOTE_OVERRIDES.get("nse_index") == {})
check("crypto's vote set does not include either", not any("Bollinger" in v for v in config.VOTE_OVERRIDES["crypto"].get("add", [])))
check("textbook settings, 20 and 2", config.BOLLINGER_LENGTH == 20 and config.BOLLINGER_MULT == 2.0)
oi = se.compute_option_chain_signal(None)
tech = dict(se.compute_technical_signal(bars([100.0 + 0.3 * i for i in range(80)])), bb_score=-1, bb_break_score=1)
r = se.build_recommendation("SENSEX", tech, oi, 100)
check("off: no Bollinger vote in the count", "Bollinger" not in r["votes"] and "Bollinger break" not in r["votes"], r["votes"])
was = config.VOTE_OVERRIDES
try:
    config.VOTE_OVERRIDES = dict(was, nse_index={"add": ["Bollinger"]})
    r = se.build_recommendation("SENSEX", tech, oi, 100)
    check("'Bollinger' added: the middle-band vote counts, read from tech['bb_score']", r["votes"].get("Bollinger") == -1,
          r["votes"])
    config.VOTE_OVERRIDES = dict(was, nse_index={"add": ["Bollinger break"]})
    r = se.build_recommendation("SENSEX", tech, oi, 100)
    check("'Bollinger break' added: read from tech['bb_break_score']", r["votes"].get("Bollinger break") == 1, r["votes"])
    check("...and for an Indian index only - crypto unaffected",
          "Bollinger break" not in se.build_recommendation("BTC", tech, oi, 100)["votes"])
finally:
    config.VOTE_OVERRIDES = was

print("4. THE BACKTEST'S FAST PATH READS THE SAME VOTES AS THE LIVE ENGINE")
import backtest_intraday as bt
df = bars([100.0 + 3 * math.sin(i / 7) + 0.02 * i for i in range(400)])
pre = bt.precompute(df, "SENSEX")
bad = bt.verify_precompute(df, pre, samples=40, index_key="SENSEX")
check("verify_precompute checks both Bollinger votes", '"bb_score", "bb_break_score"' in open(bt.__file__).read())
check("...and they match compute_technical_signal on 40 bars of a swinging series", not bad, bad[:3])
seen = {(bt.tech_at(df, pre, i)["bb_score"], bt.tech_at(df, pre, i)["bb_break_score"]) for i in range(60, 400)}
check("that series exercises every vote value (so the match above means something)",
      {(1, 1), (1, 0), (-1, 0), (-1, -1)} <= seen, sorted(seen))

print()
print("BOLLINGER VOTE TEST PASSED" if not fails else f"BOLLINGER VOTE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
