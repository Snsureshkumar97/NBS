#!/usr/bin/env python3
"""+DI/-DI as a vote - indicators.plus_minus_di(), signal_engine's di_score, and
config.DI_VOTE_MODE wired into build_recommendation(). Hand-traced.

The user, 2 Oct 2026, quoting an Investopedia guide that ADX is paired with
+DI/-DI to know which way price moves; this tool's ADX has only ever been a
strength gate. "off" (default, live) must touch nothing; "add" joins
Trend/MACD/RSI/VWAP as an extra vote; "replace_trend" takes the EMA Trend
vote's slot while leaving tech["trend_score"] itself alone. Same shape as
volume_vote_test.py.
"""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import config
import indicators as ind
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def bars(highs, lows, closes):
    idx = pd.date_range("2026-10-01 09:15", periods=len(closes), freq="15min")
    return pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes,
                         "Volume": [100] * len(closes)}, index=idx)


print("1. indicators.plus_minus_di() - HAND-TRACED ON A STRAIGHT RAMP")
# Every bar: high +2, low +2 over the last. up_move = +2, down_move = -2 each bar
# after the first, so ALL directional movement is +DM, none is -DM.
n = 40
hi = [100 + 2 * i + 1 for i in range(n)]
lo = [100 + 2 * i - 1 for i in range(n)]
cl = [100 + 2 * i for i in range(n)]
up = bars(hi, lo, cl)
p, m = ind.plus_minus_di(up, 14)
check("an unbroken rise has -DI at exactly zero (no downward movement ever)",
      abs(float(m.iloc[-1])) < 1e-9, float(m.iloc[-1]))
check("...and +DI well above it", float(p.iloc[-1]) > 20, float(p.iloc[-1]))
# +DM = 2 every bar; true range = 3 (|high - prev close|: prev close 100+2(i-1), high
# 100+2i+1). So +DI -> 100 * 2/3 = 66.67 - but only once the ewm(alpha=1/14) has
# SETTLED: after 40 bars the smoothed +DM is still ~1.89 of 2 and smoothed TR ~2.94
# of 3 (hand-traced: 2*(1-(13/14)^39) / ...), i.e. ~64.2. 300 bars is settled to <1e-9.
long_up = bars([100 + 2 * i + 1 for i in range(300)], [100 + 2 * i - 1 for i in range(300)],
               [100 + 2 * i for i in range(300)])
pl, ml = ind.plus_minus_di(long_up, 14)
check("settled +DI is 100 * (+DM 2) / (true range 3) = 66.67",
      abs(float(pl.iloc[-1]) - 100 * 2 / 3) < 1e-6, float(pl.iloc[-1]))
check("and the SHORT 40-bar ramp's unsettled 64.2 is what the smoothing arithmetic predicts, not the limit",
      abs(float(p.iloc[-1]) - 64.15) < 0.05, float(p.iloc[-1]))
dn = bars([200 - 2 * i + 1 for i in range(n)], [200 - 2 * i - 1 for i in range(n)],
          [200 - 2 * i for i in range(n)])
p2, m2 = ind.plus_minus_di(dn, 14)
check("the mirror: an unbroken fall has +DI at exactly zero", abs(float(p2.iloc[-1])) < 1e-9)
check("...and -DI well above it", float(m2.iloc[-1]) > 20, float(m2.iloc[-1]))

print("2. adx() IS UNCHANGED BY THE REFACTOR - SAME NUMBERS FROM THE SAME PIECES")
np.random.seed(7)
nn = 300
c = 25000 + np.cumsum(np.random.randn(nn) * 12)
rnd = bars(c + np.abs(np.random.randn(nn) * 8), c - np.abs(np.random.randn(nn) * 8), c)
pdi, mdi = ind.plus_minus_di(rnd, 14)
dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
rebuilt = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(0)
check("adx(df, 14) is exactly DX of plus_minus_di() smoothed - the two cannot drift apart",
      ind.adx(rnd, 14).equals(rebuilt))
dx3 = dx.ewm(alpha=1 / 3, adjust=False).mean().fillna(0)
check("...also with the live engine's 3-candle DX smoothing (adx(df, 14, 3))",
      ind.adx(rnd, 14, 3).equals(dx3))

print("3. signal_engine.compute_technical_signal() EXPOSES di_score FROM THE LAST BAR")
t_up = se.compute_technical_signal(up, "NIFTY")
t_dn = se.compute_technical_signal(dn, "NIFTY")
check("a rising series: +DI > -DI -> di_score +1", t_up["di_score"] == 1, t_up["di_score"])
check("a falling series: -DI > +DI -> di_score -1", t_dn["di_score"] == -1, t_dn["di_score"])
check("the raw readings ride along for display", t_up["plus_di"] > t_up["minus_di"], (t_up["plus_di"], t_up["minus_di"]))
check("di_score is NOT folded into total_score/max_score - a vote only when a mode adds it",
      t_up["max_score"] == 4 and t_up["total_score"] == (t_up["trend_score"] + t_up["macd_score"]
                                                          + t_up["rsi_score"] + t_up["vwap_score"]))
flat = bars([100.0] * 40, [100.0] * 40, [100.0] * 40)
check("no directional movement at all (dead flat): di_score 0, not a crash",
      se.compute_technical_signal(flat, "NIFTY")["di_score"] == 0)

print("4. DI_VOTE_MODE = 'off' (THE DEFAULT): A di_score IS PRESENT BUT CHANGES NOTHING")
STEP = config.INSTRUMENTS["NIFTY"]["strike_step"]
OI_UNAVAILABLE = se.compute_option_chain_signal(None)
STRICT = config.strictness()


def tech(trend=1, macd=1, rsi=1, vwap=1, di=None, adx=25, close=24000.0, macd_hist=5.0):
    t = {"last_close": close, "last_rsi": 55.0, "last_atr": 50.0,
         "trend_score": trend, "macd_score": macd, "macd_hist": macd_hist,
         "rsi_score": rsi, "vwap_score": vwap, "adx": adx,
         "adx_ok": adx >= config.ADX_TREND_THRESHOLD, "vwap": close - 5,
         "vwap_gap": 5.0, "last_swing_low": close - 200, "last_swing_high": close + 200,
         "total_score": trend + macd + rsi + vwap, "max_score": 4}
    if di is not None:
        t["di_score"] = di
    return t


was_mode = config.DI_VOTE_MODE
config.DI_VOTE_MODE = "off"
t = tech(trend=1, macd=1, rsi=-1, vwap=-1, di=1)          # 2 v 2, dead even
rec = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP)
check("the fixture is genuinely dead-even - NEUTRAL", rec["raw_bias"] == "NEUTRAL", rec["raw_bias"])
check("'off' leaves the votes exactly the usual four - no +DI/-DI",
      "+DI/-DI" not in rec["votes"] and len(rec["votes"]) == 4, rec["votes"])

print("5. DI_VOTE_MODE = 'add': +DI/-DI JOINS AS A FIFTH VOTE, TREND STAYS")
config.DI_VOTE_MODE = "add"
t = tech(trend=1, macd=0, rsi=1, vwap=0, di=1)            # only 2 voted: short of min_agree
config.DI_VOTE_MODE = "off"
without = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP)
config.DI_VOTE_MODE = "add"
check(f"the premise: 2 of 4 voted, short of min_agree={STRICT['min_agree']} - NEUTRAL",
      without["raw_bias"] == "NEUTRAL", without["raw_bias"])
rec = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP)
check("Trend is still one of the votes", rec["votes"].get("Trend") == 1, rec["votes"])
check("+DI/-DI is now ALSO a vote, a genuine fifth", rec["votes"].get("+DI/-DI") == 1, rec["votes"])
check("5 votes in all", len(rec["votes"]) == 5, rec["votes"])
check("trend, RSI and +DI/-DI agree: the third agreeing vote clears min_agree, 0 dissent - BULLISH",
      rec["raw_bias"] == "BULLISH", rec["raw_bias"])

print("6. DI_VOTE_MODE = 'replace_trend': +DI/-DI TAKES TREND'S SLOT, NOT A FIFTH")
config.DI_VOTE_MODE = "replace_trend"
t = tech(trend=1, macd=1, rsi=-1, vwap=-1, di=-1)
rec = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP)
check("Trend is GONE from the votes - replaced, not kept alongside",
      "Trend" not in rec["votes"], rec["votes"])
check("+DI/-DI sits in its place", rec["votes"].get("+DI/-DI") == -1, rec["votes"])
check("still 4 votes - a straight swap", len(rec["votes"]) == 4, rec["votes"])
check("macd(+1) against di/rsi/vwap(-1 each): 1 v 3 - BEARISH",
      rec["raw_bias"] == "BEARISH", rec["raw_bias"])
check("tech['trend_score'] itself is untouched (display and anything else that reads it)",
      t["trend_score"] == 1)

print("7. A tech DICT WITH NO di_score AT ALL (an older caller) ABSTAINS, NOT A CRASH")
config.DI_VOTE_MODE = "add"
t = tech(trend=1, macd=1, rsi=1, vwap=1)                  # no di key
rec = se.build_recommendation("NIFTY", t, OI_UNAVAILABLE, STEP)
check("the vote is present and abstaining (0), the usual four still decide",
      rec["votes"].get("+DI/-DI") == 0 and rec["raw_bias"] == "BULLISH", rec["votes"])

config.DI_VOTE_MODE = was_mode
check("the mode is back to its shipped default 'off' after the test", config.DI_VOTE_MODE == "off")

print()
if fails:
    print(f"DI VOTE TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("DI VOTE TEST PASSED")
