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
readout = SRC[SRC.index("function chLegend(readout){"):]
readout = readout[:readout.index("\n}\n")]
keys = SRC[SRC.index("function chKeys(d, C){"):]
keys = keys[:keys.index("\n}\n")]
check("the readout keeps the prices, the keys row the indicators",
      "O <b>" in readout and "EMA " not in readout and "EMA ${d.ema_fast_len||20}" in keys and "Supertrend" in keys)
# (8 Oct 2026: the tags are the chart library's own price-line labels, sized to their words, kept apart on the axis)
check("a level's tag is as wide as its words: the library's own label, titled with the level's name",
      "axisLabelVisible: true, title: a.label}" in SRC and "cx.fillRect(" not in SRC)
check("Zerodha's own chart is a link on the toolbar, opening in a new tab, hidden until there is one",
      '<a class="lbtn kitebtn" id="cvkite" target="_blank" rel="noopener noreferrer" hidden' in pane
      and 'id="cvkite2"' in pane and "a.lbtn.kitebtn[hidden]{display:none}" in SRC)
check("a taller chart on a desktop", "@media (min-width:901px){#cv{height:clamp(430px,66vh,720px)}}" in SRC)
check("Today's range shows where the price sits, drawn", 'tile("Where it is now"' in SRC and '<span class="rngbar"><i style="left:' in SRC)
check("...as an unboxed strip in the Zerodha look", ':root[data-look="kite"] #trendtiles .tile{background:transparent;border:0' in SRC)
check("no chart-tab text under 12px", not [x for x in re.findall(r"\.(?:chartkeys|rngbar)[^{]*\{[^}]*font-size:(\d+)px", SRC) if int(x) < 12])

print()
print("CHART LAYOUT TEST PASSED" if not fails else f"CHART LAYOUT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
