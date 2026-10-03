#!/usr/bin/env python3
"""config.VOTE_OVERRIDES - a market's own entry-vote set - wired into
signal_engine.build_recommendation(), plus the two extra reads it can add
(st_score, vol_score). Hand-traced. The user, 2 Oct 2026: "just try to improve
btc with delta exchange with what votes we have to enter the trades change that".
Empty overrides (every market today) must change nothing; a crypto override must
never reach the Indian indices."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import config
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


OI = se.compute_option_chain_signal(None)


def tech(trend=1, macd=1, rsi=1, vwap=1, st=0, vol=0, di=0, adx=25, close=84000.0):
    return {"last_close": close, "last_rsi": 55.0, "last_atr": 300.0,
            "trend_score": trend, "macd_score": macd, "macd_hist": 5.0 if macd >= 0 else -5.0,
            "rsi_score": rsi, "vwap_score": vwap, "di_score": di, "st_score": st, "vol_score": vol,
            "adx": adx, "adx_ok": adx >= config.ADX_TREND_THRESHOLD, "vwap": close - 50,
            "vwap_gap": 50.0, "last_swing_low": close - 800, "last_swing_high": close + 800,
            "total_score": trend + macd + rsi + vwap, "max_score": 4}


def rec(key, t):
    step = config.INSTRUMENTS[key]["strike_step"]
    return se.build_recommendation(key, t, OI, step)


was = {k: dict(v) for k, v in config.VOTE_OVERRIDES.items()}

print("1. THE LOOKUP - EMPTY FOR EVERY MARKET TODAY, AND PER MARKET WHEN SET")
check("BTC (crypto) has no override today", config.vote_overrides("BTC") == {})
check("NIFTY has no override today", config.vote_overrides("NIFTY") == {})
config.VOTE_OVERRIDES["crypto"] = {"drop": ["VWAP"]}
check("a crypto override is seen by BTC", config.vote_overrides("BTC") == {"drop": ["VWAP"]})
check("...and NOT by NIFTY, SENSEX or BANKNIFTY",
      all(config.vote_overrides(k) == {} for k in ("NIFTY", "SENSEX", "BANKNIFTY")))
check("a returned override is a copy - editing it cannot change the setting",
      (lambda d: (d.update({"x": 1}), config.vote_overrides("BTC") == {"drop": ["VWAP"]})[1])(config.vote_overrides("BTC")))
config.VOTE_OVERRIDES["crypto"] = {}

print("1b. AN INSTRUMENT'S OWN OVERRIDE - GOLD'S ADX 25 (3 Oct 2026), ON TOP OF ITS MARKET'S")
check("GOLD carries its own ADX 25", config.vote_overrides("GOLD") == {"adx": 25}, config.vote_overrides("GOLD"))
check("BTC, in the same crypto market, does NOT - still empty", config.vote_overrides("BTC") == {})
check("the Indian indices do not either", all(config.vote_overrides(k) == {} for k in ("NIFTY", "BANKNIFTY", "SENSEX")))
config.VOTE_OVERRIDES["crypto"] = {"drop": ["VWAP"], "adx": 30}
check("an instrument's own value wins over its market's, and the market's other keys still apply",
      config.vote_overrides("GOLD") == {"drop": ["VWAP"], "adx": 25} and config.vote_overrides("BTC") == {"drop": ["VWAP"], "adx": 30},
      config.vote_overrides("GOLD"))
config.VOTE_OVERRIDES["crypto"] = {}
rg = rec("GOLD", tech(adx=22, close=3800.0))
rb = rec("BTC", tech(adx=22))
check("ADX 22: GOLD is blocked by its own 25 gate, BTC at the same 22 is not (its gate is still 20)",
      rg["adx_blocked"] and not rb["adx_blocked"], (rg["adx_blocked"], rb["adx_blocked"]))
check("...and its wait message names ITS gate - 'ADX=22 < 25', not the global 20 it is above",
      "ADX=22 < 25" in rg["action"] and "< 20" not in rg["action"], rg["action"])

print("2. EMPTY OVERRIDES CHANGE NOTHING")
t = tech(trend=1, macd=1, rsi=-1, vwap=-1, st=1, vol=1, di=1)
r = rec("BTC", t)
check("the votes are exactly the usual four - the extra reads are present but not votes",
      sorted(r["votes"]) == ["MACD", "RSI", "Trend", "VWAP"], r["votes"])
check("2 v 2 stays NEUTRAL", r["raw_bias"] == "NEUTRAL", r["raw_bias"])

print("3. 'drop' REMOVES A VOTE OUTRIGHT")
config.VOTE_OVERRIDES["crypto"] = {"drop": ["VWAP"]}
r = rec("BTC", tech(trend=1, macd=1, rsi=1, vwap=-1))
check("VWAP is gone from the votes", "VWAP" not in r["votes"] and len(r["votes"]) == 3, r["votes"])
check("its dissent is gone with it: 3 agree, 0 against - BULLISH", r["raw_bias"] == "BULLISH", r["raw_bias"])

print("4. 'add' BRINGS IN AN EXTRA READ AS A VOTE")
config.VOTE_OVERRIDES["crypto"] = {"add": ["Supertrend", "Volume"]}
t = tech(trend=1, macd=0, rsi=1, vwap=0, st=1, vol=0)
r = rec("BTC", t)
check("Supertrend joins as a vote carrying st_score, Volume as one carrying vol_score",
      r["votes"].get("Supertrend") == 1 and r["votes"].get("Volume") == 0, r["votes"])
check("trend + RSI + Supertrend = 3 agreeing, 0 against - BULLISH (the usual four alone could not: 2 voted)",
      r["raw_bias"] == "BULLISH", r["raw_bias"])
config.VOTE_OVERRIDES["crypto"] = {"add": ["+DI/-DI"]}
check("+DI/-DI can be added the same way", rec("BTC", tech(di=-1))["votes"].get("+DI/-DI") == -1)
config.VOTE_OVERRIDES["crypto"] = {"add": ["NotARealVote"]}
check("an unknown name is ignored, not a crash", sorted(rec("BTC", tech())["votes"]) == ["MACD", "RSI", "Trend", "VWAP"])

print("5. 'min_agree' / 'max_dissent' / 'adx' OVERRIDE THE STRICTNESS - FOR THIS MARKET ONLY")
config.VOTE_OVERRIDES["crypto"] = {"min_agree": 4, "max_dissent": 0}
r = rec("BTC", tech(trend=1, macd=1, rsi=1, vwap=-1))
check("3 for, 1 against no longer fires when 4-of-4 is required", r["raw_bias"] == "NEUTRAL", r["raw_bias"])
check("...but unanimity still does", rec("BTC", tech(trend=1, macd=1, rsi=1, vwap=1))["raw_bias"] == "BULLISH")
config.VOTE_OVERRIDES["crypto"] = {"adx": 30}
r = rec("BTC", tech(adx=25))
check("ADX 25 is now below crypto's gate of 30 - blocked", r["bias"] == "NEUTRAL" and r.get("adx_blocked"),
      (r["bias"], r.get("adx_blocked")))
r = rec("NIFTY", tech(adx=25, close=25000.0))
config.VOTE_OVERRIDES["crypto"] = {}
r0 = rec("NIFTY", tech(adx=25, close=25000.0))
# (NIFTY's final bias here is held by its separate no-option-chain check either way - so the
# test is that crypto's ADX gate leaves NIFTY's own reading exactly as it was, not BULLISH.)
check("NIFTY at the same ADX 25 is untouched by crypto's gate - not ADX-blocked, and identical "
      "to having no crypto override at all",
      not r["adx_blocked"] and (r["bias"], r["raw_bias"], r["blockers"]) == (r0["bias"], r0["raw_bias"], r0["blockers"]),
      (r["adx_blocked"], r["raw_bias"]))

print("6. A CRYPTO OVERRIDE NEVER CHANGES AN INDIAN INDEX'S VOTES")
config.VOTE_OVERRIDES["crypto"] = {"drop": ["VWAP", "RSI"], "add": ["Supertrend"], "min_agree": 4}
r = rec("NIFTY", tech(trend=1, macd=1, rsi=1, vwap=-1, st=-1, close=25000.0))
check("NIFTY still votes on its four, Supertrend not among them",
      sorted(r["votes"]) == ["MACD", "RSI", "Trend", "VWAP"], r["votes"])
check("...and decides on today's strict rules (3 v 1 fires)", r["raw_bias"] == "BULLISH", r["raw_bias"])

print("7. THE EXTRA READS THEMSELVES (compute_technical_signal)")
n = 120
idx = pd.date_range("2026-09-01", periods=n, freq="15min", tz="Asia/Kolkata")
c = 80000 + np.arange(n) * 20.0                      # a steady rise
vol = np.full(n, 10.0); vol[-1] = 30.0              # last candle on 3x volume
df = pd.DataFrame({"Open": c - 5, "High": c + 10, "Low": c - 10, "Close": c, "Volume": vol}, index=idx)
t = se.compute_technical_signal(df, "BTC")
check("a steady rise sits above its Supertrend line: st_score +1", t["st_score"] == 1, t["st_score"])
check("an up candle (close > open) on 3x the last 20 candles' volume: vol_score +1", t["vol_score"] == 1)
df2 = df.copy(); df2.iloc[-1, df2.columns.get_loc("Volume")] = 14.0     # only 1.4x
check("1.4x volume is ordinary - abstains (0)", se.compute_technical_signal(df2, "BTC")["vol_score"] == 0)
df3 = df.copy(); df3["Volume"] = 0.0
check("an index with no volume of its own always abstains",
      se.compute_technical_signal(df3, "NIFTY")["vol_score"] == 0)
df4 = df.copy(); df4.iloc[-1, df4.columns.get_loc("Open")] = c[-1] + 15  # a down candle on the big volume
check("a DOWN candle on 3x volume votes -1", se.compute_technical_signal(df4, "BTC")["vol_score"] == -1)

config.VOTE_OVERRIDES.clear()
config.VOTE_OVERRIDES.update(was)
check("the shipped setting is back: every market empty", all(v == {} for v in config.VOTE_OVERRIDES.values()))

print()
if fails:
    print(f"VOTE OVERRIDES TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("VOTE OVERRIDES TEST PASSED")
