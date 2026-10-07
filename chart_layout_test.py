#!/usr/bin/env python3
"""The Chart tab's layout (the user, 7 Oct 2026: "check the chart tab layout as well"): one legend - the readout and the
buttons on the toolbar, the indicator keys on a slim row under it, the old fixed legend under the canvas gone; a level's
tag as wide as its words (a "Room ↑ 83,846" tag used to run off the canvas); a taller chart on a desktop; and Today's
range with where the price sits in it, drawn - as an unboxed strip in the Zerodha look."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "web_server.py")).read()

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

pane = SRC[SRC.index('<section class="pane" data-pane="chart">'):]
pane = pane[:pane.index("</section>")]
check("one legend: the keys' own row under the toolbar, no fixed legend under the canvas",
      '<div class="chartkeys" id="cvkeys"></div>' in pane and 'class="legend"' not in pane and "Up candle" not in pane)
draw = SRC[SRC.index("// ---- the OHLC readout, in the bar above the canvas"):]
draw = draw[:draw.index("\n}\n")]
readout, keys = draw.split("const keys = $(\"cvkeys\");")
check("the readout keeps the prices, the keys row the indicators",
      "O <b>" in readout and "EMA " not in readout and "EMA ${d.ema_fast_len||20}" in keys and "Supertrend" in keys)
check("a level's tag is as wide as its words, reaching into the plot only when it must",
      "Math.max(PAD.r, Math.ceil(cx.measureText(txt).width) + 8)" in SRC and "cx.fillRect(w-tw, a.ty-8, tw, 16);" in SRC
      and "cx.fillRect(w-PAD.r, a.ty-8, PAD.r, 16);" not in SRC)
check("a taller chart on a desktop", "@media (min-width:901px){#cv{height:clamp(430px,66vh,720px)}}" in SRC)
check("Today's range shows where the price sits, drawn", 'tile("Where it is now"' in SRC and '<span class="rngbar"><i style="left:' in SRC)
check("...as an unboxed strip in the Zerodha look", ':root[data-look="kite"] #trendtiles .tile{background:transparent;border:0' in SRC)
check("no chart-tab text under 12px", not [x for x in re.findall(r"\.(?:chartkeys|rngbar)[^{]*\{[^}]*font-size:(\d+)px", SRC) if int(x) < 12])

print()
print("CHART LAYOUT TEST PASSED" if not fails else f"CHART LAYOUT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
