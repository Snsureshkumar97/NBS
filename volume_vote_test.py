#!/usr/bin/env python3
"""config.VOLUME_VOTE_MODE, wired into signal_engine.build_recommendation() -
hand-traced. "off" (default) must touch nothing; "add" joins Trend/MACD/
RSI/VWAP as a fifth vote; "replace_macd" takes MACD's vote slot while
leaving the SEPARATE MACD_MUST_AGREE veto (tech["macd_score"] itself)
completely unaffected - that veto was independently validated and this
request was about the vote, not it.
"""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


STEP = config.INSTRUMENTS["NIFTY"]["strike_step"]
OI_UNAVAILABLE = se.compute_option_chain_signal(None)
STRICT = config.strictness()


def tech(trend=1, macd=1, rsi=1, vwap=1, adx=25, close=24000.0, macd_hist=5.0):
    """A hand-built tech dict - build_recommendation() only ever reads these
    keys as plain values, so there is no need to run real indicators over
    real price data to test its OWN voting/vetoing logic in isolation."""
    return {"last_close": close, "last_rsi": 55.0, "last_atr": 50.0,
            "trend_score": trend, "macd_score": macd, "macd_hist": macd_hist,
            "rsi_score": rsi, "vwap_score": vwap, "adx": adx,
            "adx_ok": adx >= config.ADX_TREND_THRESHOLD, "vwap": close - 5,
            "vwap_gap": 5.0, "last_swing_low": close - 200, "last_swing_high": close + 200,
            "total_score": trend + macd + rsi + vwap, "max_score": 4}


was_mode = config.VOLUME_VOTE_MODE
was_macd_agree = config.MACD_MUST_AGREE

print("1. VOLUME_VOTE_MODE = 'off' (THE DEFAULT): A VOLUME DICT IS GIVEN BUT CHANGES NOTHING")
config.VOLUME_VOTE_MODE = "off"
t = tech(trend=1, macd=1, rsi=-1, vwap=-1)      # 2 vs 2, dead even - the fixture's own premise
without = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP, volume=None)
withvol = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP,
                                   volume={"available": True, "volume_score": 1})
check("the fixture is genuinely dead-even without volume - NEUTRAL",
      without["raw_bias"] == "NEUTRAL", without["raw_bias"])
check("'off' ignores the volume dict entirely - identical votes either way",
      withvol["votes"] == without["votes"] and "Volume" not in withvol["votes"], withvol["votes"])
check("...and so the same NEUTRAL result", withvol["raw_bias"] == "NEUTRAL", withvol["raw_bias"])

print("2. VOLUME_VOTE_MODE = 'add': VOLUME JOINS AS A FIFTH VOTE, MACD STAYS")
config.VOLUME_VOTE_MODE = "add"
# trend+RSI bullish, MACD+VWAP abstain: only 2 of 4 actually voted, short of
# min_agree(3) on its own - the fixture's own premise for "not enough yet".
t = tech(trend=1, macd=0, rsi=1, vwap=0)
without = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP, volume=None)
check("the fixture's own premise: only 2 of 4 voted, not enough to clear "
      f"min_agree={STRICT['min_agree']} on their own - NEUTRAL",
      without["raw_bias"] == "NEUTRAL", without["raw_bias"])
rec = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP,
                               volume={"available": True, "volume_score": 1})
check("MACD is still one of the votes (abstaining, as given)", rec["votes"].get("MACD") == 0, rec["votes"])
check("Volume is now ALSO a vote, a genuine fifth", rec["votes"].get("Volume") == 1, rec["votes"])
check("5 votes total", len(rec["votes"]) == 5, rec["votes"])
check(f"the bullish volume vote is the THIRD agreeing vote (trend, RSI, volume) - "
      f"clears min_agree={STRICT['min_agree']} with 0 dissenting - BULLISH",
      rec["raw_bias"] == "BULLISH", rec["raw_bias"])

print("3. VOLUME_VOTE_MODE = 'replace_macd': VOLUME TAKES MACD'S VOTE SLOT, NOT A SIXTH")
config.VOLUME_VOTE_MODE = "replace_macd"
t = tech(trend=1, macd=1, rsi=-1, vwap=-1)
rec = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP,
                               volume={"available": True, "volume_score": -1})
check("MACD is GONE from the votes - it was replaced, not kept alongside",
      "MACD" not in rec["votes"], rec["votes"])
check("Volume sits in its place", rec["votes"].get("Volume") == -1, rec["votes"])
check("still 4 votes total - a straight swap, not a sixth", len(rec["votes"]) == 4, rec["votes"])
check("trend(+1) and MACD's old vote now cancelled by volume(-1) against vwap/rsi(-1 each): "
      "1 vs 3 - BEARISH", rec["raw_bias"] == "BEARISH", rec["raw_bias"])

print("4. EITHER MODE: volume['available']=False IS TREATED EXACTLY LIKE volume=None")
config.VOLUME_VOTE_MODE = "add"
t = tech(trend=1, macd=1, rsi=-1, vwap=-1)
rec = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP,
                               volume={"available": False, "volume_score": 1})
check("no Volume vote appears - unavailable means unavailable, same as PCR's own handling",
      "Volume" not in rec["votes"] and rec["raw_bias"] == "NEUTRAL", rec["votes"])

print("5. replace_macd DOES NOT TOUCH THE SEPARATE MACD_MUST_AGREE VETO - "
      "THAT IS AN INDEPENDENTLY VALIDATED MECHANISM, NOT PART OF THIS REQUEST")
config.VOLUME_VOTE_MODE = "replace_macd"
config.MACD_MUST_AGREE = True
# trend/rsi/vwap all bullish (3), volume also bullish - a clean BULLISH setup
# EXCEPT the real MACD histogram (tech["macd_score"]/macd_hist) itself disagrees.
t = tech(trend=1, macd=-1, rsi=1, vwap=1, macd_hist=-3.0)
# reach must be marked available, or the chain-missing check masks the MACD
# veto entirely (reports "no chain" instead) before it ever gets a chance to
# fire - exactly the masking bug a comment right above macd_blocked's own
# definition in signal_engine.py describes fixing for a DIFFERENT gate.
rec = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP,
                               reach={"available": True},
                               volume={"available": True, "volume_score": 1})
check("votes alone would be unanimous BULLISH (MACD isn't even voting any more)",
      all(v == 1 for v in rec["votes"].values()), rec["votes"])
check("but the raw MACD histogram still disagrees, and the veto still fires - NEUTRAL, blocked",
      rec["bias"] == "NEUTRAL" and any("Momentum disagrees" in b for b in rec["blockers"]),
      (rec["bias"], rec["blockers"]))
config.MACD_MUST_AGREE = was_macd_agree

config.VOLUME_VOTE_MODE = was_mode

print()
if fails:
    print(f"VOLUME VOTE TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("VOLUME VOTE TEST PASSED")
