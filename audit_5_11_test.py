#!/usr/bin/env python3
"""Screen audit items 5-11 (16 Sep 2026), pinned.

5 line icons instead of emoji   6 sidebar groups that fold
7 44px phone targets            8 a bigger chart
9 a compact no-signal state     10 the client ID masked
11 keyboard and screen-reader basics

Source checks. The rendered page was measured in a browser at 1440x900 and
375x812 when these were made.
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


nav = SRC[SRC.index('<nav class="menu" id="tabs"'):]
nav = nav[:nav.index("</nav>")]
bot = SRC[SRC.index('<nav class="botnav"'):]
bot = bot[:bot.index("</nav>")]

print("5. ICONS")
emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]|&#(1[0-2]\\d{4}|9\\d{3});")
check("no emoji or emoji entities in the sidebar", not emoji.search(nav), emoji.findall(nav)[:4])
check("none in the bottom bar", not emoji.search(bot), emoji.findall(bot)[:4])
check("every sidebar item has a line icon", nav.count('class="tab') == nav.count('<svg class="ico"')
      - nav.count('class="chev"'), (nav.count('class="tab'), nav.count('<svg class="ico"')))
check("icons are decorative to a screen reader", '<svg class="ico"' in nav and 'aria-hidden="true"' in nav)
check("icons take the text colour", 'stroke="currentColor"' in nav)
desk = SRC[SRC.index("const DESK = ["):]
desk = desk[:desk.index("];")]
check("the Home desk cards use them too", "<svg class='ico'" in desk and not emoji.search(desk))
check("sign-out has an icon and a name", 'title="Sign out" aria-label="Sign out"><svg' in SRC)

print("6. SIDEBAR GROUPS")
desk_part = nav[:nav.index('data-grp="market"')]
for t in ("signal", "chart", "chain", "journal"):
    check(f"{t} is always visible, outside any folding group", f'data-tab="{t}"' in desk_part)
for g, tabs in (("market", ("market", "pulse", "screener", "sector", "spikes")),
                ("analysis", ("vol", "greeks", "levels", "internals", "strength", "season")),
                ("research", ("news", "record"))):
    part = nav[nav.index(f'data-grp="{g}"'):]
    part = part[:part.index("</div>\n  </div>")]
    check(f"{g} folds and holds its pages", all(f'data-tab="{t}"' in part for t in tabs))
check("default: Market open, Analysis and Research folded",
      'GDEF = {market: true, analysis: false, research: false}' in SRC)
check("the choice is kept per browser", 'localStorage.setItem(GKEY, JSON.stringify(NAVG))' in SRC)
check("the group holding the current page always opens",
      'const g = act && act.closest(".mgrp");' in SRC and "if(g) navGroup(g, true);" in SRC)
check("each fold button says whether it is open", 'b.setAttribute("aria-expanded", open ? "true" : "false")' in SRC)

print("7. PHONE TARGETS")
check("controls are at least 44px tall on a phone",
      ":is(button,.lbtn,select,.menu .tab,.navbtn,.hd .chip,input:not([type=checkbox]):not([type=radio])){min-height:44px}" in SRC)
check("...including Switch market, which is a link styled as a chip", 'class="chip" id="mktsw"' in SRC)
check("drag handles are gone on touch screens", "@media(hover:none){.grip{display:none}}" in SRC)
check("the one small text link has a bigger hit area",
      'class="honestlink"' in SRC and ".honestlink{display:inline-block;padding:14px 4px;margin:-14px 0}" in SRC)

print("8. THE CHART")
check("full width on its page", '.pane[data-pane="chart"] .grid{grid-template-columns:1fr}' in SRC)
check("today's range sits above it as a strip", '.pane[data-pane="chart"] #colR{order:-1}' in SRC)
check("most of the screen's height on a desktop", '.pane[data-pane="chart"] #cv{height:clamp(380px,58vh,760px)}' in SRC)
check("unchanged on a phone", '@media(max-width:640px){.pane[data-pane="chart"] #cv{height:330px}}' in SRC)

print("9. NO SIGNAL")
check("one sentence instead of four empty rows",
      'if(!rungs.some(x => x[1] != null)){' in SRC and 'class="ladempty">No targets or stop while there is no signal.' in SRC)
check("it says when checking resumes", "Checking resumes when the market opens." in SRC)

print("10. THE CLIENT ID")
check("never printed in full on Home", "Connected to Zerodha as ${k.user_id}." not in SRC)
check("masked to its last two characters", 'String(k.user_id).slice(-2)' in SRC)

print("11. KEYBOARD AND SCREEN READERS")
body = SRC[SRC.index("</head><body>"):]
check("a skip link is the first thing to tab to",
      body.index('<a class="skip" href="#main">Skip to content</a>') < body.index("<aside"))
check("...and it goes somewhere", '<div class="main" id="main" tabindex="-1">' in SRC)
check("a focus ring in either look",
      ":is(button,a,select,input,summary,[tabindex]):focus-visible{outline:2px solid var(--accent);outline-offset:2px}" in SRC)
check("card titles are headings", SRC.count('<p class="eyebrow" role="heading" aria-level="2"') >= 50)
check("the selected section is announced", 'b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false")' in SRC)

print("AUDIT 5-11 TEST PASSED" if not fails else f"AUDIT 5-11 TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
