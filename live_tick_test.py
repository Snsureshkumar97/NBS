#!/usr/bin/env python3
"""The page's fastest price loop no longer pauses on document.hidden.

Asked for by the user on 22 Sep 2026 ("its moving but the numbers are not
moving fast as it used to"): document.hidden reads true in most browsers not
just when a tab is switched away from, but whenever the window isn't the OS's
frontmost one - a second monitor, a window beside another app - which is a
completely normal way to keep a live price screen up. priceTick() (4/s) used
to pause there, leaving only tick()'s 3-second full-state poll moving, which
looked like the whole page had slowed down rather than stopped. Fixed by
dropping that one guard; every other tab's own lazy refresh (still guarded,
correctly - no reason to poll a tab that is not even open) is checked here too,
so a future edit does not silently widen or narrow the fix. Source-string
checks only; no server, no browser."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


SRC = open(os.path.join(HERE, "web_server.py")).read()

print("1. THE FAST PRICE LOOP RUNS WHETHER OR NOT THE WINDOW IS FOCUSED")
a = SRC.index("async function priceTick(){")
b = SRC.index("async function tick(){")
body = SRC[min(a, b):max(a, b)] if a < b else SRC[a:SRC.index("\n}", a) + 2]
priceTick_body = SRC[a:SRC.index("\nasync function tick(){") if "\nasync function tick(){" in SRC[a:] else len(SRC)]
# slice to the end of the function: its closing brace is the next one at the start of a line
end = SRC.index("\n}\n", a) + 3
priceTick_body = SRC[a:end]
check("priceTick() no longer returns early on document.hidden", "if(document.hidden) return;" not in priceTick_body, priceTick_body[:160])
check("...and it still runs 4 times a second, unchanged", "priceTick(); setInterval(priceTick,250);" in SRC)
check("the comment explains why, so it is not silently reintroduced", "also reads hidden in most browsers" in priceTick_body, priceTick_body[:400])

print("2. THE SLOW FULL-STATE POLL WAS NEVER GUARDED, AND STILL ISN'T")
tick_start = SRC.index("async function tick(){")
tick_body = SRC[tick_start:SRC.index("\n}", tick_start) + 2]
check("tick() has no document.hidden check of its own - it never needed the fix", "document.hidden" not in tick_body, tick_body)
check("...and it still runs every 3 seconds", "tick(); setInterval(tick,3000);" in SRC)

print("3. EVERYTHING ELSE THAT SHOULD STILL PAUSE FOR A TAB NOT EVEN OPEN, STILL DOES")
still_guarded = [
    ('the watchlist tab', 'if(TAB === "watchlist" && !document.hidden) watchFetch();'),
    ('the AI trades tab', 'if(TAB === "aidesk" && !document.hidden) aiFetch();'),
    ('the Gann tab', 'if(TAB === "gann" && !document.hidden) gannFetch();'),
    ('the strike chart popup', 'if(OC.open && !document.hidden) ocPnl();'),
]
for label, needle in still_guarded:
    check(f"{label}'s own refresh is untouched - it only matters while that tab or popup is open", needle in SRC, label)

print("LIVE TICK TEST PASSED" if not fails else f"LIVE TICK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
