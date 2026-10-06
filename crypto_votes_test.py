#!/usr/bin/env python3
"""The crypto market's entry since 6 Oct 2026 (the user: "for the crypto market votes i want to remove them and add EMA
20/50 vwap macd and adx gate above 25 only these and room to run with risk reward back to 1:1"): no special rule for
BTC or gold - the engine, voting Trend (EMA 20/50), MACD and VWAP only, all three agreeing, ADX 25+, the room-to-run
check at 1:1, out at one target at 1:1. The Indian market untouched."""
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

print("1. THE SETTINGS")
check("no special rule for BTC or gold any more (the engine decides)", config.cfd_rule("BTC") is None and config.cfd_rule("GOLD") is None)
check("...the retired rules kept for a rollback", "BTC" in config.CFD_RULES_RETIRED and "GOLD" in config.CFD_RULES_RETIRED)
check("EMA 20 / 50 behind the Trend vote", (config.EMA_FAST, config.EMA_SLOW) == (20, 50))
for k in ("BTC", "GOLD"):
    vo = config.vote_overrides(k)
    check(f"{k}: RSI dropped, all of the rest must agree, none against, ADX 25", vo.get("drop") == ["RSI"]
          and vo.get("min_agree") == 3 and vo.get("max_dissent") == 0 and vo.get("adx") == 25, vo)
    check(f"{k}: one target at 1:1", config.cfd_exit_plan(k) == {"plain_r": 1.0})
check("room to run at 1:1 on crypto (the ticket gate's reward:risk)", config.MIN_REWARD_RISK_T3["crypto"] == 1.0)
check("the Indian indices untouched", config.vote_overrides("NIFTY") == {} and config.cfd_exit_plan("NIFTY") is None
      and config.MIN_REWARD_RISK_T3["nse_index"] == 1.0)

print("2. THE ENGINE ON A BTC READING")
NO_CHAIN = se.compute_option_chain_signal(None)
def tech(trend=1, macd=1, rsi=1, vwap=1, adx=30.0, close=85000.0):
    return {"last_close": close, "last_rsi": 55.0, "last_atr": 300.0, "ema_fast": close - 50, "ema_slow": close - 150,
            "trend_score": trend, "macd_score": macd, "macd_hist": 5.0 * macd, "rsi_score": rsi, "vwap_score": vwap,
            "di_score": 0, "st_score": 0, "vol_score": 0, "plus_di": 25.0, "minus_di": 15.0, "adx": adx,
            "adx_ok": adx >= config.ADX_TREND_THRESHOLD, "supertrend": close - 600, "vwap": close - 100,
            "vwap_gap": 100.0, "last_swing_low": close - 700, "last_swing_high": close + 700,
            "total_score": trend + macd + rsi + vwap, "max_score": 4}
REACH = {"available": True, "reach_up": 2000.0, "reach_down": 2000.0, "cap_up": "day range", "cap_down": "day range"}
def rec(**kw):
    return se.build_recommendation("BTC", tech(**kw), NO_CHAIN, config.INSTRUMENTS["BTC"]["strike_step"], reach=REACH)
r = rec()
check("Trend + MACD + VWAP all up, ADX 30: a BUY", r["bias"] == "BULLISH" and r["option_type"] == "CE", r["bias"])
votes = (r.get("votes") or r.get("vote_breakdown") or {})
check("...decided on exactly Trend, MACD and VWAP - RSI has no say", r["bias"] == rec(rsi=-1)["bias"] == "BULLISH",
      rec(rsi=-1)["bias"])
for name, kw in (("VWAP against", {"vwap": -1}), ("MACD against", {"macd": -1}), ("Trend against", {"trend": -1}),
                 ("VWAP silent", {"vwap": 0})):
    check(f"{name}: no trade (all three must agree)", rec(**kw)["bias"] == "NEUTRAL", rec(**kw)["bias"])
check("all three agree but ADX 24: no trade (the gate is 25)", rec(adx=24.0)["bias"] == "NEUTRAL")
check("ADX 25: trades", rec(adx=25.0)["bias"] == "BULLISH")
s = rec(trend=-1, macd=-1, vwap=-1, rsi=-1)
check("all three down: a SELL", s["bias"] == "BEARISH" and s["option_type"] == "PE")
check("the exit: one target at 1x the stop's distance", r["target_basis"] == "plain_r"
      and abs((r["index_targets"][2] - r["spot"]) - r["risk_points"]) < 0.02, (r["index_targets"], r["spot"], r["risk_points"]))
low = dict(REACH, reach_up=100.0)
nr = se.build_recommendation("BTC", tech(), NO_CHAIN, config.INSTRUMENTS["BTC"]["strike_step"], reach=low)
check("room to run: a market that can only travel 100 against a bigger stop - no trade", nr["bias"] == "NEUTRAL"
      and nr.get("not_worth_it"), (nr["bias"], nr.get("blocked_reason")))

print()
print("CRYPTO VOTES TEST PASSED" if not fails else f"CRYPTO VOTES TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
