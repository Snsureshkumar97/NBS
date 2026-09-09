#!/usr/bin/env python3
"""
rail_test.py — the left rail, at every window size

The rail is the one strip that is ALWAYS on screen, so an icon that slides
under the theme toggle at 720px is an icon you can never press. The spacing is
solved rather than fixed, and this is what proves the solution holds — at the
smallest window the app allows and at 4K.
"""
import sys

sys.path.insert(0, "/tmp/tkstub")
sys.path.insert(0, "/home/claude/trading-tool")

import tkinter as tk
import gui
import skin
from PIL import ImageFont

_fc = {}


def _pil(t, px, bold=False):
    f = _fc.get((px, bool(bold)))
    if f is None:
        f = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
            % ("-Bold" if bold else ""), int(px))
        _fc[(px, bool(bold))] = f
    return f.getbbox(t)[2]


skin.install_measurer(_pil)

root = tk.Tk()
app = gui.SignalApp(root)
app.popup_var.set(False)
root.bell = lambda: None
sk = app.bridge.skin

WANT = ["board", "map", "review", "alerts", "settings"]
SIZES = [("MacBook Air 13 window", 1180, 720), ("very short", 1600, 700),
         ("MacBook Air 13 FULL", 1470, 823), ("MacBook Pro 14", 1512, 916),
         ("1080p FULL", 1920, 1080), ("1440p FULL", 2560, 1440),
         ("4K FULL", 3840, 2160)]

fails = []
for name, W, H in SIZES:
    sk.canvas._w, sk.canvas._h = W, H
    app.bridge._painted = False
    app.bridge.flush()

    rail = {}
    for x0, y0, x1, y1, key, fn in sk._hits:
        if isinstance(key, tuple) and len(key) == 2 and key[0] == "rail":
            rail[key[1]] = (y0, y1)

    missing = [k for k in WANT if k not in rail]
    ys = [rail[k] for k in WANT if k in rail]
    overlap = any(ys[i][1] > ys[i + 1][0] for i in range(len(ys) - 1))
    theme_y = rail.get("theme", (H - 180, H - 128))[0]
    collide = bool(ys) and ys[-1][1] > theme_y
    off = bool(ys) and ys[-1][1] > H

    status = "ok"
    if missing:
        status = f"MISSING {missing}"
    elif overlap:
        status = "ICONS OVERLAP EACH OTHER"
    elif collide:
        status = f"LAST ICON RUNS INTO THE THEME TOGGLE (y1={ys[-1][1]:.0f} > {theme_y:.0f})"
    elif off:
        status = f"LAST ICON IS OFF SCREEN (y1={ys[-1][1]:.0f} > H={H})"

    print(f"  {name:24s} {W}x{H}  "
          f"{len(ys)}/{len(WANT)} icons  last y1={ys[-1][1]:.0f}  theme at {theme_y:.0f}  {status}")
    if status != "ok":
        fails.append((name, status))

print()
if fails:
    for f in fails:
        print("PROBLEM:", f)
    raise SystemExit(1)
print("RAIL TEST PASSED — every icon reachable, no collisions, every size")
