#!/usr/bin/env python3
"""Midcap Select as a PAPER-ONLY fourth Indian index (the user, 7 Oct 2026: "add midcap i will check paper trades").
Its contract facts; that no live order can ever be placed for it; that its option chain is found by its own underlying
name (the old fixed three-index map raised for anything else) while the other three still resolve; the recorder keeps
its premiums; the engine prices its strikes on a 25-point step."""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import data_providers as dp
import live_orders as lo
import record_options
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. THE INDEX")
m = config.INSTRUMENTS.get("MIDCPNIFTY") or {}
check("an Indian index, after the other three", config.instruments_in("nse_index") == ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"])
check("lot 120, strike step 25, on NSE as 'NIFTY MID SELECT', options named MIDCPNIFTY",
      (m.get("lot_size"), m.get("strike_step"), m.get("kite_exchange"), m.get("kite_tradingsymbol"), m.get("nse_symbol"))
      == (120, 25, "NSE", "NIFTY MID SELECT", "MIDCPNIFTY"), m)

print("2. PAPER ONLY")
check("not one of the live-order indices", "MIDCPNIFTY" not in lo.INDICES and lo.INDICES == ("NIFTY", "BANKNIFTY", "SENSEX"))
ex = lo.Executor("me@example.invalid", os.path.join(tempfile.mkdtemp(), "trades.csv"), kite=lambda: None, start=False)
try:
    ex.set_enabled("MIDCPNIFTY", True)
    refused = False
except ValueError:
    refused = True
check("switching live orders on for it is refused", refused and "MIDCPNIFTY" not in ex.enabled)
check("...and an entry for it sends nothing (no switch to be on)", not ex.switches("rule").get("MIDCPNIFTY"))
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("the page shows the live switch only for an index the live orders know", "!(CUR in (L.enabled || {}))" in src)

print("3. ITS OPTION CHAIN")
today = dt.date.today()
def inst(name, strike, kind, days=20, exch="NFO"):
    return {"name": name, "instrument_type": kind, "strike": strike, "expiry": today + dt.timedelta(days=days),
            "tradingsymbol": f"{name}{strike}{kind}", "instrument_token": hash((name, strike, kind)) % 10 ** 7, "exchange": exch}
rows = {"NFO": [inst("MIDCPNIFTY", 13750, "CE"), inst("MIDCPNIFTY", 13750, "PE"), inst("NIFTY", 22600, "CE", 6),
                inst("BANKNIFTY", 55000, "PE"), inst("MIDCPNIFTY", 13700, "CE", -3)],
        "BFO": [inst("SENSEX", 72600, "CE", 1, "BFO")]}
real = dp._instruments_cached
dp._instruments_cached = lambda kite, exchange: rows[exchange]
try:
    p = object.__new__(dp.KiteDataProvider)
    p.kite, p._option_inst_cache, p._instrument_cache = None, {}, {}
    ex_, got = p._all_option_instruments("MIDCPNIFTY")
    check("Midcap's options are found by their own name (it used to raise KeyError)", ex_ == "NFO"
          and {(i["strike"], i["instrument_type"]) for i in got} == {(13750, "CE"), (13750, "PE")}, [i["tradingsymbol"] for i in got])
    check("...an expired one is still dropped", all(i["expiry"] >= today for i in got))
    check("Nifty, Bank Nifty and Sensex still resolve to theirs",
          [i["name"] for i in p._all_option_instruments("NIFTY")[1]] == ["NIFTY"]
          and [i["name"] for i in p._all_option_instruments("BANKNIFTY")[1]] == ["BANKNIFTY"]
          and p._all_option_instruments("SENSEX")[0] == "BFO" and [i["name"] for i in p._all_option_instruments("SENSEX")[1]] == ["SENSEX"])
finally:
    dp._instruments_cached = real

print("4. THE RECORDER, THE AI DESK AND THE ENGINE")
import ai_desk
class _F:
    def instruments(self): return config.instruments_in("nse_index")
d = object.__new__(ai_desk.AIDesk)
d.feed = _F()
check("the AI desk does not ask the model about Midcap (no calls billed for it)", d._names() == ["NIFTY", "BANKNIFTY", "SENSEX"], d._names())
check("the option recorder keeps Midcap's real premiums too", "MIDCPNIFTY" in record_options.INDICES)
tech = {"last_close": 13757.2, "last_rsi": 45.0, "last_atr": 30.0, "ema_fast": 13740.0, "ema_slow": 13780.0, "trend_score": -1,
        "macd_score": -1, "macd_hist": -2.0, "rsi_score": -1, "vwap_score": -1, "di_score": 0, "st_score": 0, "vol_score": 0,
        "plus_di": 15.0, "minus_di": 25.0, "adx": 26.0, "adx_ok": True, "vwap": 13770.0, "vwap_gap": -12.8,
        "last_swing_low": 13700.0, "last_swing_high": 13790.0, "total_score": -4, "max_score": 4}
rec = se.build_recommendation("MIDCPNIFTY", tech, se.compute_option_chain_signal(None), m["strike_step"],
                              reach={"available": True, "reach_up": 120.0, "reach_down": 120.0, "cap_up": "day range", "cap_down": "day range"})
check("the engine reads it like the others: a put on a strike on the 25-point step", rec["bias"] == "BEARISH"
      and rec["option_type"] == "PE" and rec["suggested_strike"] % 25 == 0, (rec["bias"], rec.get("suggested_strike")))

print()
print("MIDCAP INDEX TEST PASSED" if not fails else f"MIDCAP INDEX TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
