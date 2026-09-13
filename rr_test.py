"""Risk & reward charges: split so any lot count is exact, from the same cost
model the backtests used, and only where they mean something."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, feeds, regime_study as rs

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. SCALING TO ANY NUMBER OF LOTS IS EXACT")
entry, exits = 120.0, [150.0, 175.0, 200.0, 90.0]
for name in ("NIFTY", "SENSEX"):
    lot = config.INSTRUMENTS[name]["lot_size"]
    ch = feeds._trade_charges(name, entry, exits, lot)
    exch = config.INSTRUMENTS[name]["kite_exchange"]
    worst = 0.0
    for lots in (1, 2, 3, 5, 10):
        for key, px in zip(("t1", "t2", "t3", "stop"), exits):
            direct = (px - entry) * lot * lots - rs.net_rupees(entry, px, lot * lots, exch, 0.0)
            scaled = ch["flat"] + ch["per_lot"][key] * lots
            worst = max(worst, abs(direct - scaled))
    check(f"{name} ({exch}): flat + per-lot x lots == the backtest cost model", worst < 0.05, f"worst gap Rs {worst:.4f}")
    print(f"      {name}: flat Rs {ch['flat']}, per lot at T2 Rs {ch['per_lot']['t2']}, at stop Rs {ch['per_lot']['stop']}")
n = feeds._trade_charges("NIFTY", entry, exits, 75)
s = feeds._trade_charges("SENSEX", entry, exits, 75)
check("BSE and NSE are charged at their own rates", n["per_lot"]["t2"] != s["per_lot"]["t2"])
check("flat part is brokerage both sides plus GST", abs(n["flat"] - 2 * 20 * 1.18) < 1e-9, n["flat"])

print("2. ONLY WHERE THEY MEAN SOMETHING")
check("BTC: none (Deribit fees are different)", feeds._trade_charges("BTC", 4391.0, [4600., 4800., 4900., 4290.], 1) is None)
meta = config.INSTRUMENTS["NIFTY"]
live = {"premium_source": "live", "live_ltp": 120.0, "premium_targets": [150., 175., 200.], "premium_stop_loss": 90.0}
check("live premium: charged", feeds._live_charges("NIFTY", live, meta) is not None)
check("approximated premium: not charged", feeds._live_charges("NIFTY", dict(live, premium_source="approx_move"), meta) is None)
check("missing stop still prices the targets", feeds._live_charges("NIFTY", dict(live, premium_stop_loss=None), meta)["per_lot"]["stop"] is None)

print("3. AN OPEN PREMIUM TICKET CARRIES THEM")
Feed = next(c for c in vars(feeds).values() if isinstance(c, type) and hasattr(c, "_tickets_with_odds"))
tk = {"open": True, "tracked_on": "premium", "entry": 120.0, "targets": [150., 175., 200.], "stop": 90.0,
      "lot_size": 75, "index_targets": None, "index_stop": None, "hit": {}, "sl_hit": False}
fake = types.SimpleNamespace(
    state={"indices": {"NIFTY": {"public": {"spot": 24500.0, "odds": {}}},
                       "BANKNIFTY": {"public": {"spot": 56000.0, "odds": {}}}}},
    tickets=types.SimpleNamespace(public=lambda k: {"ticket": dict(tk, tracked_on="premium" if k == "NIFTY" else "index"), "wait": None}))
snap = Feed._tickets_with_odds(fake)
check("premium ticket: charges attached", (snap["NIFTY"]["ticket"].get("charges") or {}).get("flat") == round(2 * 20 * 1.18, 2))
check("index-tracked ticket: none (no rupee price to charge on)", "charges" not in snap["BANKNIFTY"]["ticket"])

print()
print("RISK & REWARD TEST PASSED" if not fails else f"RISK & REWARD TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
