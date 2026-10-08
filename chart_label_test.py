#!/usr/bin/env python3
"""Every tag on the chart must be readable on the colour behind it.

16 Sep 2026: the crosshair price and time tags were painted with --ink - a
near-white grey - and then written in white, so the number could not be seen at
all. This checks the tags painted here ask onColour() for their text (since 8 Oct
2026 only the P&L badge - the library draws the rest), and re-does
onColour()'s arithmetic here against the real theme colours: the colour it picks
must be the higher-contrast of black and white, and must clear WCAG AA (4.5:1).
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


def lum(hexcol):
    h = hexcol.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
    f = lambda v: (v / 255) / 12.92 if v / 255 <= 0.03928 else (((v / 255) + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


DARK = "#0a0d14"
pick = lambda bg: DARK if contrast(DARK, bg) >= contrast("#ffffff", bg) else "#ffffff"

print("1. THE TAGS ASK FOR A READABLE COLOUR")
# Since 8 Oct 2026 the index chart is TradingView's Lightweight Charts: the level, last-price and crosshair tags are the
# library's own, and it picks each label's text by contrast with the tag. The one tag still painted here is the open
# ticket's P&L badge over the chart - it asks onColour() as the canvas tags did.
start = SRC.index("// THE CHART")
chart = SRC[start:SRC.index('cv.addEventListener("dblclick"', start)]
check("no tag is hand-painted on a canvas any more - the library draws them", "fillStyle" not in chart and "fillText" not in chart,
      re.findall(r".*fill(?:Style|Text).*", chart)[:2])
check("onColour() exists", "function onColour(bg)" in SRC)
check("the P&L badge takes its text from its own background", "el.style.color = onColour(bg);" in chart)

print("2. THE COLOUR IT PICKS IS ACTUALLY READABLE")
themes = re.findall(r"--ink:(#[0-9a-fA-F]{3,6});.*?\n\s*--up:(#[0-9a-fA-F]{3,6}); --down:(#[0-9a-fA-F]{3,6});", SRC, re.S)
check("both themes' colours found in the stylesheet", len(themes) >= 2, f"{len(themes)} theme(s)")
for n, (ink, up, down) in enumerate(themes[:2], 1):
    for label, bg in (("--ink (the crosshair tag)", ink), ("--up", up), ("--down", down)):
        chosen = pick(bg)
        r = contrast(chosen, bg)
        check(f"theme {n} {label} {bg}: {chosen} reads on it", r >= 4.5, f"contrast {r:.1f}:1")
    white_on_ink = contrast("#ffffff", ink)
    check(f"theme {n}: white on {ink} really was unreadable - the bug this fixes",
          white_on_ink < 1.6, f"contrast {white_on_ink:.2f}:1")

print("3. THE ARITHMETIC AGREES WITH THE BROWSER'S")
for bg, expect in (("#e8e8ec", DARK), ("#f0f2f6", DARK), ("#0a0d14", "#ffffff"), ("#2be08a", DARK)):
    check(f"{bg} -> {'dark' if expect == DARK else 'white'} text", pick(bg) == expect)

print("CHART LABEL TEST PASSED" if not fails else f"CHART LABEL TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
