"""The bridge still drives the repainted skin, hover included."""
import sys, queue
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, skin, skin_bridge, inspect

# every callback name the skin can fire must exist in the bridge's map
src = inspect.getsource(skin)
import re
wanted = set(re.findall(r'self\.cb\.get\("(\w+)"', src))
bsrc = inspect.getsource(skin_bridge)
provided = set(re.findall(r'"(on_\w+)"\s*:', bsrc))
missing = wanted - provided
print("skin asks for:", len(wanted), "callbacks; bridge provides:", len(provided))
assert not missing, f"skin fires callbacks the bridge never wires: {sorted(missing)}"

# and every zone key survives a state change (start/stop, tab switch, page switch)
cb = {k: (lambda *a: None) for k in wanted}
root = tk.Tk(); s = skin.Skin(root, cb); s.repaint()
for change in ({"running": True}, {"view": "Chart"}, {"rail": "map"},
               {"rail": "board"}, {"running": False}, {"index": "BANKNIFTY"}):
    s.set(**change)
    keys = [h[4] for h in s._hits]
    assert all(k in s._zones for k in keys), f"after {change}: orphan hit target"
    n0 = len(s.canvas.find_all())
    for k in list(s._zones):
        s._set_hover(k)
    s._set_hover(None)
    assert abs(len(s.canvas.find_all()) - n0) <= 4, f"after {change}: item drift"
print("state changes keep every control hoverable and clickable")
print("\nBRIDGE TEST PASSED")
