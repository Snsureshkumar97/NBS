"""Portfolio strip: every index's money in one place, booked + live."""
import sys, os, csv, tempfile, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, trade_log, theme as T

# The portfolio asks the log about TODAY, so the test has to agree on what
# today is — otherwise it is checking a day with no trades in it.
today_dt = dt.datetime(2026, 8, 24, 11, 30, 0)
gui.now_ist = lambda: today_dt

# --- a trades.csv with this morning already in it -----------------------
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "trades.csv")
today = "2026-08-24"
with open(path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=trade_log.FIELDS)
    w.writeheader()
    for idx, pnl in (("NIFTY", 2100.0), ("NIFTY", -600.0), ("SENSEX", 3170.0),
                     ("BANKNIFTY", -450.0)):
        w.writerow({"event": "CLOSE", "date": today, "index": idx, "pnl": pnl,
                    "lots": 1, "lot_size": 20})
    w.writerow({"event": "CLOSE", "date": "2026-08-21", "index": "NIFTY",
                "pnl": 99999.0, "lots": 1})       # a DIFFERENT day, must be ignored
trade_log._log_path = lambda: path

booked, n = trade_log.booked_today(today, path)
print("booked today:", booked, "| closed trades:", n)
assert booked == {"NIFTY": 1500.0, "SENSEX": 3170.0, "BANKNIFTY": -450.0}, booked
assert n == 4, n
print("-> yesterday's 99,999 is correctly excluded")

root = tk.Tk(); app = gui.SignalApp(root)
app.popup_var.set(False); root.bell = lambda: None
app.lots_var.set("2")

# --- one index still running --------------------------------------------
app._focus = "BANKNIFTY"
app.active_trade = {"index": "BANKNIFTY", "option_type": "CE", "strike": 52000,
    "entry_time": "11:02:00", "entry_spot": 51900.0, "entry_ltp": 180.0,
    "use_premium": True, "lot_size": 30, "lots": 2,
    "index_targets": [52100.,52200.,52300.], "index_sl": 51800.,
    "premium_targets": [220.,260.,300.], "premium_sl": 150.,
    "hit": {"T1": False,"T2": False,"T3": False},
    "hit_time": {"T1": None,"T2": None,"T3": None},
    "sl_hit": False, "sl_hit_time": None, "status": "OPEN"}
app.last_rec = {"option_chain": {"available": True, "expiry": today, "strikes": [
    {"strike": 52000, "call_ltp": 210.0, "put_ltp": 5.0, "call_oi": 1, "put_oi": 1}]},
    "spot": 51980.0}
app._focus = app.current

pf = app.portfolio()
print("\nportfolio:")
for r in pf["rows"]:
    print(f"   {r['index']:10s} booked={r['booked']} open={r['open']} total={r['total']}")
print(f"   booked {pf['booked']:+,.0f} | open {pf['open']:+,.0f} | NET {pf['net']:+,.0f}")

# open leg: (210 - 180) * 30 lot_size * 2 lots = 1,800
assert pf["open"] == 1800.0, pf["open"]
assert pf["booked"] == 4220.0, pf["booked"]
assert pf["net"] == 6020.0, pf["net"]
bn = [r for r in pf["rows"] if r["index"] == "BANKNIFTY"][0]
assert bn["total"] == 1350.0, bn          # -450 booked + 1,800 open
assert bn["in_trade"] is True
assert pf["open_count"] == 1 and pf["closed_count"] == 4
print("-> booked and open are summed per index and combined")

# --- booked survives a restart ------------------------------------------
root2 = tk.Tk(); app2 = gui.SignalApp(root2)
pf2 = app2.portfolio()
assert pf2["booked"] == 4220.0, "a restart zeroed the day"
assert pf2["open"] == 0.0 and pf2["open_count"] == 0
print("-> a restart keeps the day's booked P&L (read from disk, not memory)")

# --- it reaches the screen, at every width ------------------------------
sk = app.bridge.skin
app.bridge.flush()
assert sk.state["portfolio"]["net"] == 6020.0
for W, H in [(1180,720),(1440,900),(1570,1002),(1920,1080),(2560,1440)]:
    sk.canvas._w, sk.canvas._h = W, H
    app.bridge._painted = False
    app.bridge.flush()
    txt = [str(i.opts.get("text","")) for i in sk.canvas._items if i.kind=="text"]
    assert any("+Rs.6,020" in t for t in txt), f"{W}x{H}: no combined total"
    assert any(t == "TODAY" for t in txt), f"{W}x{H}: no TODAY label"
    for i in sk.canvas._items:
        if i.kind == "text" and "Rs.6,020" in str(i.opts.get("text","")):
            assert i.coords[0] <= W, f"{W}x{H}: total drawn off-screen"
    chips = [t for t in txt if t.startswith(("NIFTY ","BANKNIFTY ","SENSEX "))]
    print(f"   {W}x{H}: total shown, {len(chips)} index chip(s)")
assert any(t.startswith("NIFTY ") for t in
           [str(i.opts.get("text","")) for i in sk.canvas._items if i.kind=="text"]), \
    "wide window should fit the per-index chips"

# --- a flat day must not claim a profit ---------------------------------
empty = tempfile.mkdtemp() + "/none.csv"
trade_log._log_path = lambda: empty
root3 = tk.Tk(); app3 = gui.SignalApp(root3)
pf3 = app3.portfolio()
assert pf3["net"] == 0.0 and pf3["any"] is False, pf3
print("-> no trades today reads as flat, not as a win")

print("\nPORTFOLIO TEST PASSED")

# --- the disk read must be cached, and invalidated when a trade closes ---
import time as _t
trade_log._log_path = lambda: path
root4 = tk.Tk(); app4 = gui.SignalApp(root4)
reads = {"n": 0}
_real = trade_log.booked_today
def counting(*a, **k):
    reads["n"] += 1
    return _real(*a, **k)
trade_log.booked_today = counting
app4._booked_cache = None      # construction already warmed it
for _ in range(40):
    app4.portfolio()
assert reads["n"] == 1, f"portfolio hit the disk {reads['n']} times in 40 repaints"
print("cache : 40 repaints -> 1 disk read")
app4._booked_cache = None          # what _log_trade_closed does
app4.portfolio()
assert reads["n"] == 2, "closing a trade did not refresh the total"
print("cache : a closed trade refreshes it immediately")
trade_log.booked_today = _real
print("\nPORTFOLIO CACHE PASSED")
