#!/usr/bin/env python3
"""config.ROOM_LIMITS - LIVE: "all" everywhere again since 15 Oct 2026 (the user: "no change it what it use to be") - the
room is the tightest of the expected move, the OI walls and the day's range left, as before 14 Oct. The "range" switch
(the user, 14 Oct 2026: "build the room fix", after room_fix_study.py) is kept and tested here: The expected move and the OI walls are still worked out and shown, but neither
limits nor floors the room there; crypto keeps all three; live's room equals the variant measured (the day's range on
the live normal day, no chain) on real Sensex history; the targets and the reward:risk gate grow with it."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["ENABLE_CRYPTO"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

import config
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. THE SETTING")
check("LIVE: all three limits everywhere, as before 14 Oct", config.ROOM_LIMITS == {"nse_index": "all", "crypto": "all"})
check("per instrument", [config.room_limits(k) for k in ("NIFTY", "SENSEX", "MIDCPNIFTY", "BTC", "GOLD")] == ["all"] * 5)
LIVE = dict(config.ROOM_LIMITS)
config.ROOM_LIMITS = dict(LIVE, nse_index="range")          # sections 2-5: the "range" switch, as built on 14 Oct
check("switched to 'range': the Indian indices only", [config.room_limits(k) for k in ("NIFTY", "SENSEX", "MIDCPNIFTY", "BTC", "GOLD")]
      == ["range"] * 3 + ["all"] * 2)
check("an unknown or missing instrument keeps all three", config.room_limits(None) == "all" and config.room_limits("XYZ") == "all")

print("2. A TIGHT EXPECTED MOVE AND A CLOSE WALL NO LONGER SHRINK AN INDIAN ROOM")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
now = dt.datetime(2026, 10, 14, 10, 30, tzinfo=IST)
idx = pd.date_range("2026-09-01 09:15", periods=1000, freq="15min", tz="Asia/Kolkata")
df = pd.DataFrame({"Open": 72000.0, "High": 72050.0, "Low": 71950.0, "Close": 72000.0, "Volume": 0.0}, index=idx)
chain = {"available": True, "expiry": "2026-10-15",
         "strikes": [{"strike": 72000, "call_ltp": 60.0, "put_ltp": 60.0}],     # a 120-point straddle: a tight move
         "top_call_oi_strike": 72100, "top_put_oi_strike": 71900}               # walls 100 points away
kw = dict(adx=22.0, range_stats=(600.0, 150.0), index_key="SENSEX", day_extremes=(72100.0, 71950.0))
r = se.compute_reachability(72000.0, chain, df, now, **kw)
room = 600.0 * config.range_expansion(22.0) - 150.0
check("Sensex: the room is the range left (600 x 1.25 - 150 = 600), not the expected move or the wall",
      r["reach_up"] == round(room, 2) and r["reach_down"] == round(room, 2) and "range" in r["cap_up"],
      (r["reach_up"], r["cap_up"]))
check("...and the expected move and the walls are still worked out for the card",
      r["expected_move_remaining"] and r["expected_move_remaining"] < room and r["resistance"] == 72100
      and r["support"] == 71900 and r["room_limits"] == "range", (r["expected_move_remaining"], r["resistance"]))
was = dict(config.ROOM_LIMITS)
config.ROOM_LIMITS = dict(was, nse_index="all")
ra = se.compute_reachability(72000.0, chain, df, now, **kw)
check("set back to 'all' it is the old room: the tighter expected move or wall wins",
      ra["reach_up"] < room and ra["room_limits"] == "all", (ra["reach_up"], ra["cap_up"]))
config.ROOM_LIMITS = was
kw0 = dict(kw, range_stats=(600.0, 800.0))                     # used more than 600 x 1.25: no range left, no trend day
r0 = se.compute_reachability(72000.0, chain, df, now, **kw0)
config.ROOM_LIMITS = dict(was, nse_index="all")
r0a = se.compute_reachability(72000.0, chain, df, now, **kw0)
config.ROOM_LIMITS = was
check("a used-up day: no room on 'range' (no floor at half the expected move - as tested); on 'all' the floor's 40",
      r0["reach_up"] is None and r0a["reach_up"] == round(0.5 * r0a["expected_move_remaining"], 2),
      (r0["reach_up"], r0a["reach_up"]))

print("3. CRYPTO IS UNCHANGED")
nowc = dt.datetime(2026, 10, 14, 10, 30, tzinfo=dt.timezone.utc)
cchain = {"available": True, "expiry": "2026-10-15", "strikes": [{"strike": 84000, "call_ltp": 300.0, "put_ltp": 300.0}],
          "top_call_oi_strike": None, "top_put_oi_strike": None}
dfc = pd.DataFrame({"Open": 84000.0, "High": 84300.0, "Low": 83700.0, "Close": 84000.0, "Volume": 1.0},
                   index=pd.date_range("2026-10-01", periods=1000, freq="15min", tz="UTC"))
rc = se.compute_reachability(84000.0, cchain, dfc, nowc, adx=22.0, range_stats=(6000.0, 100.0), index_key="BTC")
check("BTC: the expected move still limits its room", rc["room_limits"] == "all" and "expected move" in (rc["cap_up"] or ""),
      (rc["reach_up"], rc["cap_up"]))

print("4. LIVE'S ROOM IS THE ONE MEASURED (room_fix_study C): with a chain, as without one - on real Sensex history")
hist = os.path.join(os.path.expanduser("~/trading-tool-logs/history"), "SENSEX_15m_3y.csv")
if not os.path.exists(hist):
    check("Sensex history present", False, hist)
else:
    import backtest_intraday as bt
    h = pd.read_csv(hist, index_col=0, parse_dates=True)
    h.index = pd.DatetimeIndex(h.index).tz_convert("Asia/Kolkata")
    h = h.iloc[:4000][["Open", "High", "Low", "Close", "Volume"]]
    pre = bt.precompute(h, "SENSEX")
    rng = np.random.default_rng(9)
    bad = 0
    for i in rng.choice(np.arange(300, len(h) - 1), 150, replace=False):
        spot = float(h["Close"].iloc[i])
        K = round(spot / 100) * 100
        ch = {"available": True, "expiry": (h.index[i].date() + dt.timedelta(days=3)).isoformat(),
              "strikes": [{"strike": K, "call_ltp": 120.0, "put_ltp": 115.0}], "top_call_oi_strike": K + 200,
              "top_put_oi_strike": K - 200}
        args = dict(adx=float(pre["adx"].iloc[i]), range_stats=(float(pre["typical"].iloc[i]), float(pre["used_today"].iloc[i])),
                    index_key="SENSEX", day_extremes=(float(pre["day_high"].iloc[i]), float(pre["day_low"].iloc[i])))
        a = se.compute_reachability(spot, ch, h, h.index[i].to_pydatetime(), **args)
        b = se.compute_reachability(spot, {"available": False}, h, h.index[i].to_pydatetime(), **args)
        bad += (a["reach_up"], a["reach_down"]) != (b["reach_up"], b["reach_down"])
    check("150 random candles: the room with the chain equals the room without one", bad == 0, bad)

print("5. THE TARGETS GROW WITH IT")
tech = {"last_close": 72000.0, "last_rsi": 60.0, "last_atr": 60.0, "ema_fast": 71950.0, "ema_slow": 71900.0,
        "trend_score": 1, "macd_score": 1, "macd_hist": 5.0, "rsi_score": 1, "vwap_score": 1, "di_score": 1, "st_score": 1,
        "vol_score": 0, "plus_di": 30.0, "minus_di": 12.0, "adx": 22.0, "adx_ok": True, "vwap": 71950.0, "vwap_gap": 50.0,
        "last_swing_low": 71930.0, "last_swing_high": 72100.0, "total_score": 4, "max_score": 4}
oi0 = se.compute_option_chain_signal(None)
rec = se.build_recommendation("SENSEX", tech, oi0, 100, reach=r)
config.ROOM_LIMITS = dict(was, nse_index="all")
rec_a = se.build_recommendation("SENSEX", tech, oi0, 100, reach=ra)
config.ROOM_LIMITS = was
check("a call's T3 is the day's range left (72,600), not the expected move's", rec["bias"] == "BULLISH"
      and rec["index_targets"][2] == round(72000.0 + room, 2) and rec_a["index_targets"][2] < rec["index_targets"][2],
      (rec["index_targets"], rec_a["index_targets"]))

print("6. LIVE IS THE OLD ROOM AGAIN")
config.ROOM_LIMITS = LIVE
rl = se.compute_reachability(72000.0, chain, df, now, **kw)
check("the live room equals the old one (the tighter expected move or wall), not the range's", rl["room_limits"] == "all"
      and (rl["reach_up"], rl["reach_down"], rl["cap_up"]) == (ra["reach_up"], ra["reach_down"], ra["cap_up"])
      and rl["reach_up"] < room, (rl["reach_up"], rl["cap_up"]))
rec_l = se.build_recommendation("SENSEX", tech, oi0, 100, reach=rl)
check("...and so are the targets", rec_l["index_targets"] == rec_a["index_targets"], rec_l["index_targets"])

print()
print("ROOM LIMITS TEST PASSED" if not fails else f"ROOM LIMITS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
