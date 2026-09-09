"""Where do the level cards actually end up, across real screen sizes?"""
import sys
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, skin
from PIL import ImageFont
_fc = {}
def _pil(t, px, bold=False):
    f = _fc.get((px, bool(bold)))
    if f is None:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
                               % ("-Bold" if bold else ""), int(px))
        _fc[(px, bool(bold))] = f
    return f.getbbox(t)[2]
skin.install_measurer(_pil)

root = tk.Tk()
app = gui.SignalApp(root)
app.popup_var.set(False); root.bell = lambda: None
app.active_trade = {
    "index": "SENSEX", "option_type": "PE", "strike": 77100,
    "entry_time": "09:18:31", "entry_spot": 77218.4, "entry_ltp": 196.95,
    "use_premium": True, "lot_size": 20,
    "index_targets": [77020.,76940.,76860.], "index_sl": 77330.,
    "premium_targets": [261.31, 309.58, 357.84], "premium_sl": 148.2,
    "hit": {"T1": True, "T2": False, "T3": False},
    "hit_time": {"T1": "10:07:44", "T2": None, "T3": None},
    "sl_hit": False, "sl_hit_time": None, "status": "OPEN"}
app._render_trade_tracker(current_price=294.50)
sk = app.bridge.skin

SIZES = [
    ("MacBook Air 13 window",   1180, 720),
    ("MacBook Air 13 FULL",     1470, 823),
    ("MacBook Pro 14 FULL",     1512, 916),
    ("MacBook Pro 16 FULL",     1728, 1085),
    ("MacBook Air 13 FULL raw", 1440, 900),
    ("1080p FULL",              1920, 1080),
    ("1440p FULL",              2560, 1440),
    ("4K FULL",                 3840, 2160),
    ("short + wide",            1920,  800),
    ("very short",              1600,  700),
]
bad = []
for name, W, H in SIZES:
    sk.canvas._w, sk.canvas._h = W, H
    app.bridge._painted = False
    app.bridge.flush()
    g = sk.geom()
    # find the four level cards: the widest run of same-y rounded cards
    texts = [(i.opts.get("text"), i.coords) for i in sk.canvas._items if i.kind == "text"]
    # The ladder layout prints "T3  357.84" on one line rather than a bare
    # "T3", so match on the prefix. Matching exactly used to report every
    # tall window as MISSING while all four levels were on screen.
    def lvl(t):
        t = (t or "").strip()
        return any(t.startswith(p) for p in ("T1", "T2", "T3", "STOP"))

    names = [t for t, cds in texts if lvl(t)]
    ys = [cds[1] for t, cds in texts if lvl(t) and not (t or "").startswith("T1")]
    vis = [y for y in ys if 0 <= y <= H]
    tick = [t for t, cds in texts if "✓" in str(t)]
    status = "ok"
    if len(names) < 4:
        status = f"MISSING ({len(names)}/4 labels)"
    elif len(vis) < 3:
        status = f"OFF-SCREEN (ys={[round(y) for y in ys]}, H={H})"
    elif not tick:
        status = "NO TICK"
    ly1 = g["panel_y1"]
    print(f"  {name:24s} {W}x{H}  panel_y={g['panel_y']:.0f} panel_y1={ly1:.0f} "
          f"cardY={[round(y) for y in ys]}  {status}")
    if status != "ok":
        bad.append((name, W, H, status))
print()
if bad:
    for b in bad: print("PROBLEM:", b)
else:
    print("level cards visible at every size")
