"""No option chain must mean no ticket — not a ticket nobody checked."""
import sys, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import numpy as np, pandas as pd
import config, signal_engine as SE

rs = np.random.RandomState(5)
n = 220
idx = pd.date_range(end="2026-08-19 15:15", periods=n, freq="15min")
close = 24000 + np.cumsum(rs.randn(n) * 9 + 2.4)      # a clean uptrend
op = np.concatenate([[close[0]], close[:-1]])
df = pd.DataFrame({"Open": op, "High": np.maximum(op, close) + 8,
                   "Low": np.minimum(op, close) - 8, "Close": close,
                   "Volume": 1000}, index=idx)
tech = SE.compute_technical_signal(df)
oi = SE.compute_option_chain_signal(None)          # <- chain unavailable
step = config.INSTRUMENTS["NIFTY"]["strike_step"]

# --- with the gate on (the new default) ---------------------------------
config.REQUIRE_REACHABILITY = True
rec = SE.build_recommendation("NIFTY", tech, oi, step, reach=None)
print("raw bias        :", rec["raw_bias"])
print("action          :", rec["action"][:78])
print("blocked_reason  :", rec["blocked_reason"])
print("targets         :", rec["index_targets"])
print("target_basis    :", rec["target_basis"])
assert rec["blocked_reason"] == "no_chain", "the no-chain hold did not fire"
assert rec["not_worth_it"] is True
assert rec["index_targets"] == [None, None, None], "targets printed with no room check"
assert rec["bias"] == "NEUTRAL", "a ticket could still be issued"
assert any("chain" in b.lower() for b in rec["blockers"])
print("-> no chain, no ticket, and it says why")

# --- with the gate off, the old behaviour is still reachable ------------
config.REQUIRE_REACHABILITY = False
old = SE.build_recommendation("NIFTY", tech, oi, step, reach=None)
assert old["blocked_reason"] is None
print("-> switching REQUIRE_REACHABILITY off restores the old behaviour:",
      old["target_basis"], old["index_targets"][:1])
config.REQUIRE_REACHABILITY = True

# --- a real chain must still trade normally -----------------------------
spot = float(close[-1])
atm = round(spot / step) * step
chain = {"expiry": (dt.date(2026, 8, 27)).isoformat(), "underlying": spot,
         "rows": [{"strike": atm + k * step,
                   "call_oi": 90000 - abs(k) * 900, "put_oi": 90000 - abs(k) * 900,
                   "call_ltp": max(1.0, 160 - k * 14), "put_ltp": max(1.0, 160 + k * 14),
                   "call_volume": 5000, "put_volume": 5000}
                  for k in range(-8, 9)]}
oi2 = SE.compute_option_chain_signal(chain)
reach = SE.compute_reachability(tech["last_close"], oi2, df,
                                dt.datetime(2026, 8, 19, 11, 0), adx=tech.get("adx"))
rec2 = SE.build_recommendation("NIFTY", tech, oi2, step, reach=reach)
print("\nwith a chain    :", rec2["target_basis"], "| blocked:", rec2["blocked_reason"],
      "| reach:", rec2["reach_points"])
assert rec2["blocked_reason"] != "no_chain", "a healthy chain was wrongly held"
print("-> a healthy chain is unaffected")

print("\nCHAIN GATE TEST PASSED")
