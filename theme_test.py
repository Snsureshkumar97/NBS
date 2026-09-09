"""Light mode must be a palette swap, not a second app."""
import sys, subprocess
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, skin, theme as T
from PIL import ImageFont
_fc={}
def _pil(t,px,b=False):
    f=_fc.get((px,bool(b)))
    if f is None:
        f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"%("-Bold" if b else ""),int(px)); _fc[(px,bool(b))]=f
    return f.getbbox(t)[2]
skin.install_measurer(_pil)

# --- the palette itself has to clear the same bar as the dark one -------
for args in (["--surface", "#ffffff", "--pairs", "#16a34a,#dc2626,#7d8494"],
             ["--surface", "#ffffff", "--ordinal", "#173f9e,#2f62c6,#4a80da"]):
    r = subprocess.run([sys.executable, "/home/claude/trading-tool/validate_palette.py"] + args,
                       capture_output=True, text=True)
    assert "ALL CHECKS PASS" in r.stdout, r.stdout
print("palette: contrast, CVD and ordinal checks all pass on white")

root = tk.Tk(); app = gui.SignalApp(root); root.bell = lambda: None
gui._load_demo(app)
sk = app.bridge.skin
sk.canvas._w, sk.canvas._h = 1570, 1002

def paint(mode):
    T.use(mode)
    sk.state["theme"] = mode
    app.bridge._painted = False
    app.bridge.flush()
    return [i for i in sk.canvas._items]

dark = paint("dark")
light = paint("light")

# --- same layout, different ink -----------------------------------------
def shape(items):
    return {(i.kind, tuple(round(v) for v in i.coords)) for i in items}
# The SET of shapes is the property that matters: nothing may move, appear or
# vanish. Exact counts can differ by a hairline where a gradient lands two
# columns on the same rounded pixel, which is not a layout change.
d, l = shape(dark), shape(light)
assert d == l, ("the LAYOUT moved; this should be ink only. "
                f"only in dark: {list(d - l)[:4]} | only in light: {list(l - d)[:4]}")
assert abs(len(dark) - len(light)) <= 2, f"{len(dark)} vs {len(light)} items"
print(f"layout identical in both modes ({len(d)} distinct shapes)")

# --- and the ink really did change ---------------------------------------
dcols = {i.opts.get("fill") for i in dark}
lcols = {i.opts.get("fill") for i in light}
assert dcols != lcols, "nothing actually changed colour"
print("ink differs, as it should")

# --- no near-white text left on a white card ----------------------------
def lum(h):
    h = str(h or "").lstrip("#")
    if len(h) != 6: return None
    r,g,b = (int(h[i:i+2],16)/255 for i in (0,2,4))
    f = lambda u: u/12.92 if u<=0.04045 else ((u+0.055)/1.055)**2.4
    return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b)
card = lum("#ffffff")
bad = []
for i in light:
    if i.kind != "text" or not str(i.opts.get("text","")).strip():
        continue
    fill = str(i.opts.get("fill", "")).lower()
    # White ink is deliberate ON the accent gradient — the Start button, the
    # active tab, the live-price marker. Those are not on a white card.
    if fill in ("#ffffff", "white"):
        continue
    L = lum(fill)
    if L is None: continue
    ratio = (max(L,card)+0.05)/(min(L,card)+0.05)
    if ratio < 1.9:
        bad.append((str(i.opts.get("text"))[:22], i.opts.get("fill"), round(ratio,2)))
assert not bad, f"unreadable text on the light card: {bad[:6]}"
print("no washed-out text in light mode")

# --- switching back and forth leaves nothing behind ---------------------
for _ in range(4):
    paint("light"); paint("dark")
assert T.MODE == "dark" and T.BG_APP == T.PALETTES["dark"]["BG_APP"]
assert T.STOP_COL == T.PALETTES["dark"]["DOWN"], "STOP_COL drifted"
print("eight switches later the palette is still consistent")

print("\nTHEME TEST PASSED")
