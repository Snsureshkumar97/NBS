"""Daily brakes: stop entering, keep showing."""
import sys, os, csv, tempfile, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import pandas as pd, numpy as np
import tkinter as tk, gui, config, trade_log, main as M

TODAY = "2026-08-24"
def log_with(rows):
    path = os.path.join(tempfile.mkdtemp(), "trades.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trade_log.FIELDS); w.writeheader()
        for r in rows:
            w.writerow(r)
    trade_log._log_path = lambda: path
    return path

def app_at(hh, mm, rows=()):
    gui.now_ist = lambda: dt.datetime(2026, 8, 24, hh, mm, 0)
    log_with(list(rows))
    root = tk.Tk(); a = gui.SignalApp(root)
    a.popup_var.set(False); root.bell = lambda: None
    a._day_cache = None; a._booked_cache = None
    a.limits_var.set(True)      # the caps are opt-in now; this test is about them
    return a

OPEN = lambda i="NIFTY": {"event":"OPEN","date":TODAY,"index":i,"status":"OPEN"}
WIN  = lambda i="NIFTY": {"event":"CLOSE","date":TODAY,"index":i,
                          "status":"CLOSED — T3 hit (full target reached)","pnl":2000}
LOSS = lambda i="NIFTY": {"event":"CLOSE","date":TODAY,"index":i,
                          "status":"CLOSED — stop-loss hit","pnl":-900}

print("config: DAILY_TARGET_WINS=%d  MAX_TRADES_PER_DAY=%d  NO_NEW_TRADES_BEFORE=%s"
      % (config.DAILY_TARGET_WINS, config.MAX_TRADES_PER_DAY, config.NO_NEW_TRADES_BEFORE))

# --- 1. the pre-open / opening-auction window ---------------------------
for hh, mm, want in [(9, 5, "early"), (9, 15, "early"), (9, 19, "early"),
                     (9, 20, None), (11, 30, None)]:
    a = app_at(hh, mm)
    b = a.entry_block()
    got = b[0] if b else None
    assert got == want, f"{hh:02d}:{mm:02d} -> {got}, expected {want}"
    print(f"   {hh:02d}:{mm:02d}  {'HELD (' + b[1] + ')' if b else 'entries allowed'}")

# --- 2. two T3 wins stops the day ---------------------------------------
a = app_at(11, 0, [OPEN(), WIN()])
assert a.entry_block() is None, "one win should not stop the day"
print("\n   1 win  -> entries allowed")
a = app_at(11, 0, [OPEN(), WIN(), OPEN("SENSEX"), WIN("SENSEX")])
b = a.entry_block()
assert b and b[0] == "won", b
print("   2 wins -> HELD:", b[1], "|", b[2].splitlines()[0])

# --- wins count across ALL indices, not per index -----------------------
a = app_at(11, 0, [OPEN("NIFTY"), WIN("NIFTY"), OPEN("BANKNIFTY"), WIN("BANKNIFTY")])
assert a.entry_block()[0] == "won", "wins must be counted day-wide"
print("   wins are counted across every index, not per index")

# --- 3. the over-trading ceiling ----------------------------------------
a = app_at(11, 0, [OPEN(), LOSS(), OPEN(), LOSS(), OPEN(), LOSS()])
assert a.entry_block() is None, "3 tickets is under the cap of 4"
a = app_at(11, 0, [OPEN(), LOSS(), OPEN(), LOSS(), OPEN(), LOSS(), OPEN(), LOSS()])
b = a.entry_block()
assert b and b[0] == "maxed", b
print("\n   4 losing tickets -> HELD:", b[1], "| churn stopped even with no wins")

# --- 4. a restart cannot reset the limits -------------------------------
rows = [OPEN(), WIN(), OPEN("SENSEX"), WIN("SENSEX")]
a = app_at(11, 0, rows)
assert a.entry_block()[0] == "won"
root2 = tk.Tk(); a2 = gui.SignalApp(root2)      # brand new process, same log
a2._day_cache = None
a2.limits_var.set(True)
assert a2.entry_block()[0] == "won", "restarting the tool reset the daily limit"
print("   a restart does NOT hand you fresh limits")

# --- 5. yesterday's trades do not count ---------------------------------
a = app_at(11, 0, [{"event":"OPEN","date":"2026-08-21","index":"NIFTY","status":"OPEN"},
                   {"event":"CLOSE","date":"2026-08-21","index":"NIFTY",
                    "status":"CLOSED — T3 hit","pnl":5000}] * 3)
assert a.entry_block() is None, "yesterday's wins blocked today"
print("   yesterday's wins do not count against today")

# --- 6. blocked means NO TICKET, but the signal is still shown ----------
a = app_at(11, 0, [OPEN(), WIN(), OPEN("SENSEX"), WIN("SENSEX")])
rec = {"index": "NIFTY", "bias": "BULLISH", "option_type": "CE",
       "suggested_strike": 24500, "spot": 24480.0,
       "index_targets": [24560., 24620., 24700.], "index_stop_loss": 24400.,
       "premium_targets": [None]*3, "premium_stop_loss": None,
       "premium_source": None, "live_ltp": None, "trend": {"label": "UP", "dir": "up"},
       "candles": None, "option_chain": None}
took = a._consider_signal(rec, confirm_now=True)
assert took is False, "a blocked day still issued a ticket"
assert a.active_trade is None, "a blocked day still locked a trade"
print("\n   signal arrives on a blocked day -> no ticket, active_trade stays None")

a.last_rec = rec
a._render_trade_tracker(current_price=None)
a.bridge.flush()
t = a.bridge.skin.state["ticket"]
print("   badge   :", t["badge"])
print("   headline:", t["headline"])
print("   sub     :", t["sub"].splitlines()[0])
assert t["badge"] == "DAY DONE", t["badge"]
assert t["headline"] == "BUY CE", t["headline"]
assert "entry is held" in t["sub"]
levels = [l[1] for l in t["levels"]]
assert any(x not in ("—", "") for x in levels), f"the levels were hidden too: {levels}"
print("   levels  :", levels[:3], "-> the trade is fully shown, just not taken")

# --- 7. limits can be switched off --------------------------------------
config.DAILY_TARGET_WINS = 0; config.MAX_TRADES_PER_DAY = 0
a = app_at(11, 0, [OPEN(), WIN(), OPEN(), WIN(), OPEN(), WIN()])
assert a.entry_block() is None, "limits did not switch off"
config.DAILY_TARGET_WINS = 2; config.MAX_TRADES_PER_DAY = 4
print("\n   setting the limits to 0 disables them")

# --- 8. the on-screen switch overrides everything -----------------------
a = app_at(11, 0, [OPEN(), WIN(), OPEN("SENSEX"), WIN("SENSEX"),
                   OPEN(), LOSS(), OPEN(), LOSS(), OPEN(), LOSS()])
assert a.entry_block()[0] == "won", "switch ON should still block"
a.limits_var.set(False)
assert a.entry_block() is None, "switch OFF did not lift the caps"
print("   the 'Daily limits' tick box lifts both caps live, mid-session")

# --- 9. but it must NOT lift the quality gates --------------------------
b = app_at(9, 5, [])
b.limits_var.set(False)
blk = b.entry_block()
assert blk and blk[0] == "early", \
    "turning the caps off also removed the opening-window guard"
print("   with the caps off, 09:05 is STILL held — that guard is not a cap")

# --- 10. default is OFF, as asked ---------------------------------------
import importlib
assert config.DAILY_LIMITS_ON is False
c = app_at(11, 0, [OPEN(), WIN(), OPEN("SENSEX"), WIN("SENSEX")])
c.limits_var.set(config.DAILY_LIMITS_ON)
assert c.entry_block() is None, "caps applied even though the switch defaults off"
print("   default is OFF: two wins no longer stop the day")

print("\nDAILY LIMITS TEST PASSED")
