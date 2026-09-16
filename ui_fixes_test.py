#!/usr/bin/env python3
"""The four fixes from the 16 Sep 2026 screen audit, pinned so they stay fixed.

1. The signal verdict comes first on the Signal page, and the disclaimer is one
   line on a phone.
2. Muted grey text clears WCAG AA (4.5:1) in both palettes, and nothing on the
   page is set smaller than 12px.
3. The option chain's header rows no longer print over each other, and on a
   phone the strike column stays on screen.
4. A reload that lands straight on Option chain, Greeks or an analysis page
   runs that page's loaders again once every script exists.

Source checks only - the rendered page was checked in a browser at 1440x900
and 375x812 when these were made.
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


print("1. THE SIGNAL FIRST")
check("pinSignalFirst() exists", "function pinSignalFirst()" in SRC)
check("it runs after a saved layout is applied", "applyLayout(); pinSignalFirst(); wireDrag();" in SRC)
check("and again after a panel is dragged", "pinSignalFirst();\n      saveLayout();" in SRC)
body = SRC.split("function pinSignalFirst()")[1].split("\n}\n")[0]
check("the signal card goes to the top of its pane", "pane.insertBefore(sig, pane.firstElementChild)" in body)
check("the session tally and feed line follow it", '"session", "sfeed"' in body)
check("the disclaimer is one line on a phone until opened",
      "#riskbox:not([open]) > summary{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" in SRC)

print("2. READABLE TEXT")
palettes = re.findall(r"--bg:(#[0-9a-fA-F]{6});.*?--ink-3:(#[0-9a-fA-F]{6});", SRC, re.S)
check("both palettes found", len(palettes) >= 2, palettes)
for bg, ink3 in palettes[:2]:
    r = contrast(ink3, bg)
    check(f"--ink-3 {ink3} on {bg} clears 4.5:1", r >= 4.5, f"{r:.2f}:1")
small = re.findall(r"font-size:\s?(\d+(?:\.\d+)?)px", SRC)
under = sorted({float(s) for s in small if float(s) < 12})
check("no font-size under 12px anywhere on the page", not under, under)

print("3. THE OPTION CHAIN")
check("only the column-name header row sticks", "table.chain thead tr:first-child th{position:static}" in SRC)
check("the strike column is sticky on a phone",
      "table.chain th.k,table.chain td.k{position:sticky;left:0;right:0;z-index:2;background:#0b0d13}" in SRC)
check("the ATM strike keeps its highlight when stuck", "table.chain tr.atm td.k{background:#1b1e26}" in SRC)
check("a phone opens the chain with the strike centred", "box.scrollLeft = kc.offsetLeft - (box.clientWidth - kc.offsetWidth) / 2;" in SRC)

print("4. PAGES RESTORED ON RELOAD")
RESTORE = 'if(!TABS.includes(start)) start = "home";'
restore_at = SRC.find(RESTORE)
restore = SRC[restore_at:restore_at + 700]
check("the restored page is found", restore_at > 0)
check("while loading, the page is shown without calling its loaders",
      'document.readyState === "loading"' in restore
      and 'p.classList.toggle("on", p.dataset.pane === start)' in restore
      and restore.index('document.readyState === "loading"') < restore.index("showTab("))
check("and entered once, when every script has been read",
      'document.addEventListener("DOMContentLoaded",' in restore
      and "showTab(TAB, false)" in restore and "{once: true}" in restore)
# The state those loaders read is declared further down the page than the
# restore - which is exactly why calling them from there failed.
for name, decl in (("option clock", "const OI = {"), ("greeks", "let GK = "), ("analysis pages", "let ANA = ")):
    at = SRC.find(decl)
    check(f"{name} state is declared after the restore (the case this guards)",
          at > restore_at > 0, f"{decl.strip()} at {at}, restore at {restore_at}")

print("UI FIXES TEST PASSED" if not fails else f"UI FIXES TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
