#!/usr/bin/env python3
"""config.SL_MODE in signal_engine.build_recommendation (7 Oct 2026, "what will be the result if we make stop loss at
supertrend in indian market"): "swing" changes nothing; "supertrend" puts the stop on the index Supertrend line when
it is on the trade's side of the price; "tighter" only when that is closer. The room-to-run check must see the stop
actually set. And the backtest's fast path must carry the same line value as the live computation."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

NO_CHAIN = se.compute_option_chain_signal(None)
STEP = config.INSTRUMENTS["NIFTY"]["strike_step"]
def tech(bull=True, st=None, close=22600.0, atr=40.0):
    s = 1 if bull else -1
    return {"last_close": close, "last_rsi": 55.0 if bull else 45.0, "last_atr": atr, "ema_fast": close - 20 * s,
            "ema_slow": close - 60 * s, "trend_score": s, "macd_score": s, "macd_hist": 5.0 * s, "rsi_score": s,
            "vwap_score": s, "di_score": 0, "st_score": 0, "vol_score": 0, "plus_di": 25.0, "minus_di": 15.0,
            "adx": 28.0, "adx_ok": True, "vwap": close - 30 * s, "vwap_gap": 30.0 * s,
            "last_swing_low": close - 50, "last_swing_high": close + 50, "supertrend": st,
            "total_score": 4 * s, "max_score": 4}
REACH = {"available": True, "reach_up": 400.0, "reach_down": 400.0, "cap_up": "day range", "cap_down": "day range"}
def rec(mode, reach=REACH, **kw):
    config.SL_MODE = mode
    try:
        return se.build_recommendation("NIFTY", tech(**kw), NO_CHAIN, STEP, reach=reach)
    finally:
        config.SL_MODE = "swing"

print("1. THE DEFAULT")
check("SL_MODE is 'swing' (live unchanged)", config.SL_MODE == "swing")
base = rec("swing", st=22540.0)
swing_stop = round(22550 - 40 * config.SL_BUFFER_ATR_MULT, 2)
check("swing: the stop beyond the swing low, as before", base["index_stop_loss"] == swing_stop
      and base["risk_points"] == round(22600 - swing_stop, 2), (base["index_stop_loss"], base["risk_points"]))
check("...the same with no Supertrend value at all", rec("swing", st=None)["index_stop_loss"] == swing_stop)

print("2. STOP AT THE SUPERTREND")
r = rec("supertrend", st=22520.0)
check("a call with the line 80 below: stop ON the line, risk 80", r["index_stop_loss"] == 22520.0
      and r["risk_points"] == 80.0, (r["index_stop_loss"], r["risk_points"]))
p = rec("supertrend", bull=False, st=22680.0)
check("a put with the line 80 above: stop on the line", p["option_type"] == "PE" and p["index_stop_loss"] == 22680.0
      and p["risk_points"] == 80.0, (p["index_stop_loss"], p["risk_points"]))
w = rec("supertrend", st=22650.0)
check("a call with the line ABOVE the price (wrong side): today's swing stop", w["index_stop_loss"] == swing_stop, w["index_stop_loss"])
check("no line yet: today's swing stop", rec("supertrend", st=None)["index_stop_loss"] == swing_stop)
close_st = rec("supertrend", st=22590.0)
check("a line closer than the swing stop is used too (pure Supertrend)", close_st["index_stop_loss"] == 22590.0)

print("3. ONLY WHEN TIGHTER")
check("tighter: the line 10 below (closer than the swing stop) is used", rec("tighter", st=22590.0)["index_stop_loss"] == 22590.0)
check("tighter: the line 80 below (wider) is NOT used", rec("tighter", st=22520.0)["index_stop_loss"] == swing_stop)

print("4. ROOM TO RUN SEES THE STOP ACTUALLY SET")
small = dict(REACH, reach_up=45.0)               # the engine's room check is reach / stop >= strict min_rr (0.6)
check("reach 45 with today's 56-point stop (0.80): a trade", rec("swing", reach=small, st=22520.0)["bias"] == "BULLISH")
far = rec("supertrend", reach=small, st=22520.0)
check("reach 45 with the Supertrend's 80-point stop (0.56): no room - no trade", far["bias"] == "NEUTRAL"
      and far.get("not_worth_it"), (far["bias"], far.get("blocked_reason")))

print("5. THE BACKTEST CARRIES THE LINE")
rng = np.random.default_rng(7)
n = 400
close = 22600 + np.cumsum(rng.normal(0, 12, n))
idx = pd.date_range("2026-09-01 09:15", periods=n, freq="15min", tz="Asia/Kolkata")
df = pd.DataFrame({"Open": close - 3, "High": close + 15, "Low": close - 15, "Close": close,
                   "Volume": rng.integers(1000, 2000, n).astype(float)}, index=idx)
pre = bt.precompute(df, "NIFTY")
bad = bt.verify_precompute(df, pre, samples=12, index_key="NIFTY")
check("the fast path agrees with the live computation, the Supertrend line included", not bad, bad[:3])
check("...and actually has the line", bt.tech_at(df, pre, 300).get("supertrend") is not None)

print()
print("SL MODE TEST PASSED" if not fails else f"SL MODE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
