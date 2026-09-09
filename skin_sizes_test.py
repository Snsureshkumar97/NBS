import sys
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, skin

cb = {k: (lambda *a: None) for k in
      ("on_rail","on_toggle","on_quit","on_start","on_stop","on_index","on_view",
       "on_clear","on_log","on_mode","on_expiry","on_span","on_login","on_save","on_load")}

sizes = [(1180,720),(1280,800),(1440,900),(1570,1002),(1920,1080)]
rails = ["board","map","review","settings"]
views = ["Signal","Chart"]

for W,H in sizes:
    for rail in rails:
        for view in views:
            root = tk.Tk(); s = skin.Skin(root, cb)
            s.canvas._w, s.canvas._h = W, H
            s.state["rail"] = rail; s.state["view"] = view
            s.repaint()
            # no duplicate zone keys hiding items from redraw
            tags = [t for t,_ in s._zones.values()]
            assert len(tags) == len(set(tags)), f"{W}x{H} {rail}: duplicate zone tag"
            keys = [h[4] for h in s._hits]
            assert len(keys) == len(set(keys)), f"{W}x{H} {rail}: duplicate hit key {keys}"
            # everything clickable is redrawable
            for k in keys:
                assert k in s._zones, f"{W}x{H} {rail}: {k} not a zone"
            # hover each control, then check nothing drifted
            n0 = len(s.canvas.find_all())
            for k in list(s._zones):
                s._set_hover(k)
            s._set_hover(None)
            n1 = len(s.canvas.find_all())
            assert abs(n1-n0) <= 4, f"{W}x{H} {rail}/{view}: items {n0} -> {n1}"
            # every control still inside the window
            for x0,y0,x1,y1,k,fn in s._hits:
                assert -1 <= x0 and x1 <= W+1 and -1 <= y0 and y1 <= H+1, \
                    f"{W}x{H} {rail}: {k} at {(x0,y0,x1,y1)} is off-screen"
    print(f"  {W}x{H}: ok  ({len(s._hits)} controls, {len(s.canvas.find_all())} items)")

print("\nSIZE / PAGE SWEEP PASSED")

# --- the top-right status must stay inside the window at every width -----
import re
from PIL import ImageFont
_fc = {}
def _pil(text, px, bold=False):
    f = _fc.get((px, bool(bold)))
    if f is None:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
                               % ("-Bold" if bold else ""), int(px))
        _fc[(px, bool(bold))] = f
    return f.getbbox(text)[2]
skin.install_measurer(_pil)     # real widths, so truncation is actually exercised

LONG = "MARKET CLOSED · BANKNIFTY · last data 18 Aug 15:15  ·  LIVE 24,391.85 (-0.64%)"
for W,H in sizes:
    root = tk.Tk(); s = skin.Skin(root, cb)
    s.canvas._w, s.canvas._h = W, H
    s.state["status"] = LONG
    s.repaint()
    texts = [i for i in s.canvas._items if i.kind == "text"
             and str(i.opts.get("text","")).startswith("MARKET CLOSED")]
    assert texts, f"{W}x{H}: status line vanished"
    it = texts[0]
    shown = it.opts["text"]
    x = it.coords[0]
    assert it.opts.get("anchor") == "e", "status must hang off the right edge"
    assert x <= W, f"{W}x{H}: status anchored at {x}, past the right edge {W}"
    width = skin._tw(shown, 13)
    assert x - width > 0, f"{W}x{H}: status starts off-screen at {x-width}"
    print(f"  {W}x{H}: status fits -> {shown!r}")
print("STATUS LINE FITS AT EVERY WIDTH")

# --- and when it genuinely cannot fit, it sheds trailing segments --------
HUGE = ("MARKET CLOSED · BANKNIFTY · last data 18 Aug 15:15 · LIVE 24,391.85 "
        "· session net +1,240 · 3 trades · token ok · feed healthy · nothing pending")
root = tk.Tk(); s = skin.Skin(root, cb)
s.canvas._w, s.canvas._h = 1180, 720
s.state["status"] = HUGE
s.repaint()
it = [i for i in s.canvas._items if i.kind == "text"
      and str(i.opts.get("text","")).startswith("MARKET CLOSED")][0]
shown = it.opts["text"]
assert shown != HUGE, "an over-long status was not shortened"
assert shown.startswith("MARKET CLOSED · BANKNIFTY · last data"), \
    f"shortening ate the important part: {shown!r}"
assert it.coords[0] - skin._tw(shown, 13) > 0, "shortened status still off-screen"
print("over-long status shed its tail ->", repr(shown))
print("\nALL LAYOUT CHECKS PASSED")
