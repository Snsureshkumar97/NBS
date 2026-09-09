"""--demo must put the ladder on screen with no market and no login."""
import sys
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude")
sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, tkraster, skin, gui
from PIL import ImageFont
_fc={}
def _pil(t,px,b=False):
    f=_fc.get((px,bool(b)))
    if f is None:
        f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"%("-Bold" if b else ""),int(px)); _fc[(px,bool(b))]=f
    return f.getbbox(t)[2]
skin.install_measurer(_pil)

root = tk.Tk()
app = gui.SignalApp(root)
root.bell = lambda: None
gui._load_demo(app)                    # exactly what --demo runs
sk = app.bridge.skin
sk.canvas = tkraster.Recorder(1570, 1002)
app.bridge._painted = False
app.bridge.flush()

texts = [str(k.get("text", "")) for kind, coords, k, _id in sk.canvas.ops
         if kind == "text"]
tags = [t for t in texts if t.startswith(("T1  ", "T2  ", "T3  ", "STOP  "))]
print("ladder tags on screen:", tags)
assert len(tags) == 4, f"the ladder did not draw: {tags}"
assert any("REWARD : RISK" in t for t in texts), "no reward:risk"
assert any(t == "BUY CE" for t in texts), "no headline"
assert any("196.40" == t for t in texts), "no live price marker"
assert any("✓ 10:41:02" in t or "10:41:02" in t for t in texts), "no hit timestamp"
assert any("DEMO" in t for t in texts), "demo is not labelled as demo"
tkraster.render(sk.canvas, path="/tmp/demo.png")
print("all present; wrote /tmp/demo.png")
print("\nDEMO TEST PASSED")
