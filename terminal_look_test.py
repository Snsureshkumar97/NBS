#!/usr/bin/env python3
"""The terminal look: the default, readable on every surface, and actually still.

Source checks, plus the contrast arithmetic on the real token values. The
rendered look was compared in a browser at 1440x900 and 375x812 when it was made.
"""
import os
import re
import sys

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def lum(h):
    h = h.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


block = re.search(r':root\[data-look="terminal"\]\{(.*?)\}', SRC, re.S)
tok = dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{6})", block.group(1))) if block else {}

print("1. IT IS THE DEFAULT, SET BEFORE ANYTHING PAINTS")
check("the terminal token block exists", bool(tok), sorted(tok)[:6])
head = SRC[:SRC.index('<canvas id="bg3d"')]
check("the look is chosen in the head, before the body is drawn",
      'localStorage.getItem("nbs.look.v1")' in head and "document.documentElement.dataset.look" in head)
check("with no saved choice, or one it does not know, it is the terminal look",
      '(l === "glass" || l === "kite") ? l : "terminal"' in head
      and 'catch(e){ document.documentElement.dataset.look = "terminal"; }' in head)

print("2. EVERY GREY READS ON THE SURFACE IT SITS ON")
surfaces = {k: tok.get(k) for k in ("bg", "surface", "raised")}
for ink in ("ink", "ink-2", "ink-3"):
    for sname, s in surfaces.items():
        r = contrast(tok[ink], s)
        check(f"--{ink} {tok[ink]} on --{sname} {s} clears 4.5:1", r >= 4.5, f"{r:.2f}:1")
for col in ("up", "down", "warn", "accent"):
    for sname in ("surface", "raised"):
        r = contrast(tok[col], surfaces[sname])
        # 3:1 is the bar for bold figures and UI marks; these are also paired with +/- and words
        check(f"--{col} {tok[col]} on --{sname} clears 3:1 (4.5:1 shown)", r >= 3.0, f"{r:.2f}:1")

print("3. NOTHING MOVES THAT DOES NOT HAVE TO")
check("the background scene is hidden", ':root[data-look="terminal"] #bg3d{display:none}' in SRC)
scene = SRC[SRC.index('const cv = document.getElementById("bg3d");'):][:400]
check("...and its animation never starts (only the glass look runs it)", 'dataset.look !== "glass") return;' in scene)
tilt = SRC[SRC.index("// ------------------------------------------------------------ card tilt"):][:900]
check("card tilt never starts (only the glass look runs it)", 'dataset.look !== "glass") return;' in tilt)
check("the signal card neither sways nor fades in",
      ':root[data-look="terminal"] :is(.herocard,.wrap > *,.wrap > .herocard){animation:none}' in SRC)
check("no glass blur on the cards", "backdrop-filter:none;-webkit-backdrop-filter:none;box-shadow:none" in SRC)

print("4. THE WAY BACK")
check("a Look button on Home", 'id="lookbtn"' in SRC)
check("a command-palette entry for every other look", '"Switch to the " + LOOKS[k] + " look"' in SRC
      and 'const LOOKS = {terminal: "Dark", kite: "Zerodha", glass: "Glass"};' in SRC)
check("the choice is kept per browser", 'localStorage.setItem("nbs.look.v1", v)' in SRC)
check("the glass look is left intact to switch back to", "THE 3D LAYER" in SRC and "@keyframes mount" in SRC)

print("TERMINAL LOOK TEST PASSED" if not fails else f"TERMINAL LOOK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
