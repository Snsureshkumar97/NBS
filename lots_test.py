"""Lots: scales the money, never the signal, and freezes at entry."""
import sys
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, config, skin

root = tk.Tk(); app = gui.SignalApp(root)
app.popup_var.set(False); root.bell = lambda: None

def ticket():
    return {"index": "SENSEX", "option_type": "PE", "strike": 77100,
            "entry_time": "09:18:31", "entry_spot": 77218.4, "entry_ltp": 200.0,
            "use_premium": True, "lot_size": 20, "lots": app._lots(),
            "index_targets": [77020.,76940.,76860.], "index_sl": 77330.,
            "premium_targets": [260.0, 310.0, 360.0], "premium_sl": 150.0,
            "hit": {"T1": False,"T2": False,"T3": False},
            "hit_time": {"T1": None,"T2": None,"T3": None},
            "sl_hit": False, "sl_hit_time": None, "status": "OPEN"}

def money():
    app.bridge.flush()
    st = app.bridge.skin.state["ticket"]
    pnl = [v for lbl, v, col in st["stats"] if "LOT" in lbl][0]
    cap = [lbl for lbl, v, col in st["stats"] if "LOT" in lbl][0]
    return cap, pnl, [l[1] for l in st["levels"]]

# --- 1 lot baseline: +100 premium x 20 = +2,000 -------------------------
app.lots_var.set("1")
app.active_trade = ticket()
app._render_trade_tracker(current_price=300.0)
cap1, pnl1, lv1 = money()
print("1 lot :", cap1, "|", pnl1, "| T1", lv1[0])
assert cap1 == "1 LOT" and pnl1 == "+2,000", (cap1, pnl1)

# --- 5 lots: exactly five times the money -------------------------------
app.lots_var.set("5")
app.active_trade = ticket()
app._render_trade_tracker(current_price=300.0)
cap5, pnl5, lv5 = money()
print("5 lots:", cap5, "|", pnl5, "| T1", lv5[0])
assert cap5 == "5 LOTS", cap5
assert pnl5 == "+10,000", pnl5
assert float(pnl5.replace("+","").replace(",","")) == 5 * float(pnl1.replace("+","").replace(",",""))
# the per-level rupee figures scale too
assert "+Rs.6,000" in lv5[0], lv5[0]     # T1 260 - 200 = 60 x 20 x 5
assert "+Rs.1,200" in lv1[0], lv1[0]
print("       per-level money scales as well")

# --- the numbers that are NOT money must not move -----------------------
st1_levels = [l.split(" · ")[0] for l in lv1]
st5_levels = [l.split(" · ")[0] for l in lv5]
assert st1_levels == st5_levels, (st1_levels, st5_levels)
print("       targets/stop unchanged:", st5_levels)

# --- frozen at entry: changing the selector cannot rewrite a live ticket -
app.lots_var.set("2")
app._render_trade_tracker(current_price=300.0)
cap, pnl, _ = money()
assert cap == "5 LOTS" and pnl == "+10,000", (cap, pnl)
print("frozen:", cap, pnl, "— selector moved to 2, the open ticket did not")

# --- but a NEW ticket picks up the new selection ------------------------
app.active_trade = ticket()
app._render_trade_tracker(current_price=300.0)
cap, pnl, _ = money()
assert cap == "2 LOTS" and pnl == "+4,000", (cap, pnl)
print("new   :", cap, pnl, "— the next ticket uses the new selection")

# --- the selector is clamped to the configured range --------------------
for bad, want in (("0", 1), ("9", config.MAX_LOTS), ("x", config.DEFAULT_LOTS), ("", config.DEFAULT_LOTS)):
    app.lots_var.set(bad)
    got = app._lots()
    assert got == want, f"lots_var={bad!r} -> {got}, expected {want}"
print("clamp :  0->1, 9->%d, junk->%d" % (config.MAX_LOTS, config.DEFAULT_LOTS))

# --- the control is on screen and clickable at every width --------------
app.lots_var.set("3")
sk = app.bridge.skin
for W, H in [(1180,720),(1440,900),(1570,1002),(1920,1080),(2560,1440)]:
    sk.canvas._w, sk.canvas._h = W, H
    app.bridge._painted = False
    app.bridge.flush()
    keys = [h[4] for h in sk._hits]
    assert ("sel","lots") in keys, f"{W}x{H}: no Lots control"
    box = [h for h in sk._hits if h[4] == ("sel","lots")][0]
    assert 0 < box[0] and box[2] < W, f"{W}x{H}: Lots control off-screen {box[:4]}"
    # and it must not sit on top of the Stop button or the status line
    others = [h for h in sk._hits if h[4] != ("sel","lots") and abs(h[1]-box[1]) < 20]
    for o in others:
        assert o[2] <= box[0] or o[0] >= box[2], f"{W}x{H}: Lots overlaps {o[4]}"
    txt = [str(i.opts.get("text")) for i in sk.canvas._items if i.kind == "text"]
    assert "3" in txt, f"{W}x{H}: selector does not show its value"
print("layout:  present, on-screen and non-overlapping at 5 widths")

print("\nLOTS TEST PASSED")
