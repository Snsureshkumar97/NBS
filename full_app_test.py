"""Build the real app on the stub Tk and exercise both fixes together."""
import sys, queue, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import pandas as pd, numpy as np
import tkinter as tk
import gui

idx = pd.date_range(end="2026-08-18 15:30", periods=200, freq="15min")
base = 24000 + np.cumsum(np.random.RandomState(3).randn(200) * 8)
DF = pd.DataFrame({"Open": base, "High": base+12, "Low": base-12,
                   "Close": base, "Volume": 1000}, index=idx)

root = tk.Tk()
app = gui.SignalApp(root)
print("app built; skin canvas items:", len(app.bridge.skin.canvas.find_all()))

sk = app.bridge.skin
app.bridge.flush()
n_first = len(sk.canvas.find_all())
zones = len(sk._zones)
print("after first flush:", n_first, "items,", zones, "zones")
assert zones > 8

# --- a live-only update must NOT rebuild the chrome ----------------------
paints = {"chrome": 0, "live": 0}
_pc, _pl = sk.paint_chrome, sk.paint_live
sk.paint_chrome = lambda: (paints.__setitem__("chrome", paints["chrome"]+1), _pc())[1]
sk.paint_live   = lambda: (paints.__setitem__("live",   paints["live"]+1),   _pl())[1]

app.bridge.flush()                      # nothing changed at all
assert paints["chrome"] == 0, "an unchanged frame still rebuilt the chrome"
assert paints["live"] == 1
print("unchanged frame: chrome skipped, live redrawn")

sk.state["ticket"] = {"rows": [("T1","24100","#a79ef0")]}
app.bridge.flush()
assert paints["chrome"] == 0, "a live-only change rebuilt the chrome"
print("live-only change: chrome still skipped")

# --- a chrome change must rebuild ---------------------------------------
app.index_var.set("BANKNIFTY")
app.bridge.flush()
assert paints["chrome"] == 1, "switching index did not repaint the chrome"
print("chrome change (index switch): chrome repainted once")

# --- repaints are throttled, not fired per event -------------------------
root._after = []
for _ in range(50):
    app.bridge.touch()
assert len(root._after) == 1, f"50 updates scheduled {len(root._after)} repaints"
ms = root._after[0][0]
assert 40 <= ms <= 150, f"repaint delay {ms}ms is outside the sane band"
print(f"50 rapid updates -> 1 scheduled repaint, {ms}ms apart")

# --- hover still only touches the widget under the cursor ---------------
sk.paint_chrome, sk.paint_live = _pc, _pl
sk.repaint()
before = len(sk.canvas.find_all())
worst = 0
for x0,y0,x1,y1,key,fn in list(sk._hits):
    b = set(sk.canvas.find_all()); sk._set_hover(key); a = set(sk.canvas.find_all())
    worst = max(worst, len(b ^ a))
sk._set_hover(None)
after = len(sk.canvas.find_all())
print(f"hover: worst case {worst} items touched, out of {before} on screen")
assert worst < before / 4, "hover is still rebuilding most of the window"
assert abs(after - before) <= 4

print("\nFULL APP TEST PASSED")
