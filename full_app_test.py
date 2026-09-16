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
# Record what the bridge schedules instead of reading root._after: that
# attribute only exists on the Tk stub this test was written against, so on a
# machine with real tkinter (no /tmp/tkstub) it stayed empty and the throttle
# went unchecked. Nothing is really scheduled here - the point is the count.
sched = []
_real_after = root.after
root.after = lambda ms, fn=None, *a, **k: (sched.append((ms, fn)), "after#stub")[1]
try:
    app.bridge._pending = False
    for _ in range(50):
        app.bridge.touch()
finally:
    root.after = _real_after
assert len(sched) == 1, f"50 updates scheduled {len(sched)} repaints"
ms = sched[0][0]
assert 40 <= ms <= 150, f"repaint delay {ms}ms is outside the sane band"
assert sched[0][1] == app.bridge.flush, "the scheduled callback is not the repaint"
print(f"50 rapid updates -> 1 scheduled repaint, {ms}ms apart")
app.bridge._pending = False

# --- hover still only touches the widget under the cursor ---------------
sk.paint_chrome, sk.paint_live = _pc, _pl
sk.repaint()
before = len(sk.canvas.find_all())

# What hover promises is that ONLY the zone being left and the zone being
# entered are redrawn - so that is what this checks, by looking at which zone
# each touched item belongs to. The old check ("fewer than a quarter of the
# items on screen") was a proxy that said nothing about which items moved: the
# index chip alone owns 128 of the 946 items here, so leaving one chip for
# another legitimately touches ~260 and the proxy failed on a correct app.
def zone_of(item):
    tags = set(sk.canvas.gettags(item))
    for key, (tag, _fn) in sk._zones.items():
        if tag in tags:
            return key
    return None

worst = 0
for x0,y0,x1,y1,key,fn in list(sk._hits):
    was = sk._hover
    owners = {i: zone_of(i) for i in sk.canvas.find_all()}
    b = set(owners)
    sk._set_hover(key)
    a = set(sk.canvas.find_all())
    allowed = {k for k in (was, key) if k is not None}
    gone_from = {owners[i] for i in (b - a)}
    made_in = {zone_of(i) for i in (a - b)}
    assert gone_from <= allowed, f"hovering {key!r} deleted items of {gone_from - allowed}"
    assert made_in <= allowed, f"hovering {key!r} drew into {made_in - allowed}"
    worst = max(worst, len(b ^ a))
sk._set_hover(None)
after = len(sk.canvas.find_all())
biggest = max(len(sk.canvas.find_withtag(t)) for t, _ in sk._zones.values())
print(f"hover: worst case {worst} items touched, out of {before} on screen "
      f"(largest zone owns {biggest})")
assert worst <= 2 * biggest + 8, "hover touched more than the two zones involved"
assert abs(after - before) <= 4

print("\nFULL APP TEST PASSED")
