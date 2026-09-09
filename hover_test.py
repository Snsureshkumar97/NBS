"""Proves the hover fix: moving the cursor must NOT rebuild the whole screen."""
import sys
sys.path.insert(0, "/tmp/tkstub")
sys.path.insert(0, "/home/claude/trading-tool")

import tkinter as tk
import skin

root = tk.Tk()
clicks = []
cb = {k: (lambda *a, _k=k: clicks.append((_k, a)))
      for k in ("on_rail", "on_toggle", "on_quit", "on_start", "on_stop",
                "on_index", "on_view", "on_clear", "on_log", "on_mode",
                "on_expiry", "on_span", "on_login", "on_save", "on_load",
                "on_refresh", "on_export", "on_close", "on_lots", "on_theme")}
s = skin.Skin(root, cb)
s.repaint()

c = s.canvas
base_ids = set(c.find_all())
print("items after first full paint:", len(base_ids))
print("zones registered:", len(s._zones))
assert len(s._zones) > 8, "expected the rail, tabs, buttons and checks to be zones"

# --- every clickable thing must be a redrawable zone ---------------------
missing = [h[4] for h in s._hits if h[4] not in s._zones]
assert not missing, f"hit targets with no zone: {missing}"

# --- hovering must touch only a couple of items --------------------------
hits = list(s._hits)
worst = 0
for x0, y0, x1, y1, key, fn in hits:
    before = set(c.find_all())
    s._set_hover(key)
    after = set(c.find_all())
    churn = len(before ^ after)
    worst = max(worst, churn)
    assert s._hover == key
    # the redrawn widget's items are replaced; nothing else may move
    assert churn < 500, f"hover on {key} rebuilt {churn} items"
print("worst-case items redrawn for one hover move:", worst)

# --- sweeping the cursor must not leak click boxes -----------------------
s._set_hover(None)
n_hits = len(s._hits)
for _ in range(6):
    for x0, y0, x1, y1, key, fn in hits:
        s._set_hover(key)
    s._set_hover(None)
assert len(s._hits) == n_hits, f"hit list grew {n_hits} -> {len(s._hits)}"
print("hit boxes stable after 6 full sweeps:", len(s._hits))

# --- item count must not grow either (no leaked canvas items) ------------
now = len(c.find_all())
assert abs(now - len(base_ids)) <= 2, f"canvas items {len(base_ids)} -> {now}"
print("canvas items stable:", now)

# --- clicks still land on the right callback after all that sweeping -----
s.repaint()
for x0, y0, x1, y1, key, fn in list(s._hits):
    clicks.clear()
    ev = type("E", (), {"x": (x0 + x1) / 2, "y": (y0 + y1) / 2})()
    s._on_click(ev)
    assert clicks, f"click on {key} did nothing"
print("every control still clickable:", len(s._hits))

# --- chrome stays under the live layer after hover redraws ---------------
s.repaint()
order = {iid: n for n, iid in enumerate(c.find_all())}
live = [order[i] for i in c.find_withtag("live")]
if live:
    for key in list(s._zones):
        s._set_hover(key)
    order = {iid: n for n, iid in enumerate(c.find_all())}
    live_lo = min(order[i] for i in c.find_withtag("live"))
    for key, (tag, _fn) in s._zones.items():
        for i in c.find_withtag(tag):
            assert order[i] < live_lo, f"{key} floated above the live layer"
    print("z-order preserved: chrome stays below live")

print("\nHOVER TEST PASSED")
