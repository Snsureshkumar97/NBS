"""A hit target must be visible on the card, not just in a popup."""
import sys, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, theme as T

root = tk.Tk()
app = gui.SignalApp(root)

trade = {"index": "SENSEX", "option_type": "PE", "strike": 77100,
         "entry_time": "09:18:31", "entry_spot": 77218.4, "entry_ltp": 196.95,
         "use_premium": True, "lot_size": 20,
         "index_targets": [77020.0, 76940.0, 76860.0], "index_sl": 77330.0,
         "premium_targets": [261.31, 309.58, 357.84], "premium_sl": 148.2,
         "hit": {"T1": True, "T2": False, "T3": False},
         "hit_time": {"T1": "10:07:44", "T2": None, "T3": None},
         "sl_hit": False, "sl_hit_time": None, "status": "OPEN"}
app.active_trade = trade
app._render_trade_tracker(current_price=294.50)
app.bridge.flush()
levels = app.bridge.skin.state["ticket"]["levels"]
for lv in levels:
    print("  ", lv[0], "|", lv[1], "| prog", None if lv[3] is None else round(lv[3],2),
          "| mark", lv[4])

t1, t2, t3, sl = levels
assert t1[4] == "✓ 10:07:44", f"T1 hit not marked: {t1[4]!r}"
assert t1[2] == T.UP, "a reached target must turn green"
assert t1[3] == 1.0, "a reached target must read as complete"
assert t2[4] is None and t3[4] is None and sl[4] is None, "unhit levels must stay unmarked"
assert 0.8 < t2[3] < 0.95, f"T2 progress wrong: {t2[3]}"
assert sl[2] == T.STOP_COL and sl[3] == 0.0, "stop must be untouched"
print("hit is marked, coloured and measured")

# --- a stop-out must read red, not green --------------------------------
trade["sl_hit"], trade["sl_hit_time"] = True, "11:20:03"
trade["status"] = "CLOSED — stop-loss hit"
app._render_trade_tracker(current_price=148.0)
app.bridge.flush()
sl = app.bridge.skin.state["ticket"]["levels"][3]
assert sl[4] == "✓ 11:20:03" and sl[2] == T.DOWN, f"stop-out not shown red: {sl}"
assert app.badge_label.text.strip() == "STOPPED OUT"
print("stop-out reads red and the badge says STOPPED OUT")

# --- and the bar must not creep backwards after a hit -------------------
trade["sl_hit"] = False; trade["status"] = "OPEN"
app._render_trade_tracker(current_price=200.0)   # price fell back below T1
app.bridge.flush()
t1 = app.bridge.skin.state["ticket"]["levels"][0]
assert t1[3] == 1.0 and t1[4], "T1 un-hit itself when price came back"
print("a hit stays hit even if price retraces")

# --- it survives being painted at every window size ---------------------
sk = app.bridge.skin
for W, H in [(1180,720),(1440,900),(1570,1002),(1920,1080)]:
    sk.canvas._w, sk.canvas._h = W, H
    sk.repaint()
    texts = [str(i.opts.get("text","")) for i in sk.canvas._items if i.kind == "text"]
    assert any("✓" in t for t in texts), f"{W}x{H}: the tick vanished"
    for i in sk.canvas._items:
        if i.kind == "text" and "✓" in str(i.opts.get("text","")):
            assert i.coords[0] < W, f"{W}x{H}: tick drawn off-screen"
print("tick renders at every window size")
print("\nTARGET-HIT TEST PASSED")
