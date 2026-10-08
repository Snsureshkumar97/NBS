#!/usr/bin/env python3
"""Showing the Supertrend line the live trail (TRAIL_AFTER_T1_SUPERTREND,
nbs_supertrend_trail_keep_and_baseline_fix) already reads, after the user asked
"where can i see the supertrend in the tool" (2 Oct 2026) and it turned out to be
nowhere - config.py/signal_engine.py/tickets.py only ever used it internally.
Picked, from the three options offered: the Signal card ("Yes, chart + a value
on the Signal card").

Not an entry input anywhere here either - no vote, no score, no gate. Same
per-instrument pattern config.supertrend_params() already follows for the
trail itself (Bank Nifty's own multiplier, everything else the global
default) - this file re-checks that function because the chart's own call
site is a NEW place that could regress it (the adx line right above it in
web_server.py's _candles() uses self._current_market(), a MARKET string, not
an index key - silently wrong for any per-instrument override, since
INSTRUMENTS has no "nse_index" entry; the chart's Supertrend call site has to
use the real index key instead, or Bank Nifty's own (10, 3.0) would quietly
draw NIFTY's (10, 2.5) on screen).

Fakes only - no Zerodha/Delta call and no socket.
"""
import os
import shutil
import subprocess
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import feeds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. config.supertrend_params() - THE SAME LOOKUP THE LIVE TRAIL USES")
check("NIFTY gets the global default (10, 2.5)", config.supertrend_params("NIFTY") == (10, 2.5))
check("SENSEX gets the global default too - no override of its own", config.supertrend_params("SENSEX") == (10, 2.5))
check("BANKNIFTY gets its own, wider multiplier (10, 3.0)", config.supertrend_params("BANKNIFTY") == (10, 3.0))
check("no instrument named falls back to the global default, not a crash",
      config.supertrend_params() == (10, 2.5), config.supertrend_params())
check("an unknown instrument falls back to the global default too",
      config.supertrend_params("NOT_A_REAL_INSTRUMENT") == (10, 2.5))

print("2. feeds._public() EXPOSES IT ON THE SIGNAL CARD'S OWN PAYLOAD")
rec = {"index": "BANKNIFTY", "spot": 54000.0, "technical": {"adx": 22.0, "adx_ok": True, "supertrend": 53950.5}}
pub = feeds._public(rec, "BANKNIFTY")
check("pub['supertrend'] is exactly tech['supertrend'], not a re-derived copy",
      pub["supertrend"] == 53950.5, pub["supertrend"])
rec_none = {"index": "NIFTY", "spot": 25000.0, "technical": {"adx": 15.0, "adx_ok": False, "supertrend": None}}
check("a warm-up None passes through as None, not 0 or a crash",
      feeds._public(rec_none, "NIFTY")["supertrend"] is None)
check("no rec at all: _public() itself returns None (its own existing behaviour, unaffected)",
      feeds._public(None, "NIFTY") is None)

print("3. THE SIGNAL CARD: A VISIBLE 'SUPERTREND' TILE, NEXT TO 'TICKET GATE', NOT INSTEAD OF IT")
SRC = open("web_server.py").read()
start = SRC.index('$("tiles").innerHTML =')
tiles_block = SRC[start:SRC.index(";", SRC.index('tile("Supertrend"', start)) + 1]
check("'Ticket gate' is still there, unchanged", 'tile("Ticket gate"' in tiles_block)
check("a new 'Supertrend' tile reads r.supertrend, not a re-derived number",
      'tile("Supertrend"' in tiles_block and "r.supertrend" in tiles_block)
check("a missing reading (warm-up or no signal) shows an em dash, not null/NaN on the page",
      'r.supertrend==null?"—"' in tiles_block.replace(" ", ""))
check("coloured by where price sits relative to the line - up when spot is above, down when below",
      "r.spot>r.supertrend?" in tiles_block.replace(" ", "") and "var(--up)" in tiles_block
      and "var(--down)" in tiles_block)

print("4. THE CHART: A LINE ON THE PRICE PANE (LIKE EMA/VWAP), NOT A NEW SUB-PANE (LIKE ADX)")
draw_start = SRC.index("// THE CHART")
draw = SRC[draw_start:SRC.index('cv.addEventListener("dblclick"', draw_start)]
# (8 Oct 2026: the chart is TradingView's Lightweight Charts - each overlay is a line series on the price pane)
check("drawn as a line on the price pane, the same way as the EMA lines beside it",
      "s.st = chart.addSeries(LW.LineSeries, Object.assign({}, over, {color: C.supertrend}));" in draw
      and "s.fast = chart.addSeries(LW.LineSeries, Object.assign({}, over, {color: C.fast}));" in draw
      and "s.st.setData(chLine(d.supertrend, bars));" in draw)
check("included in the price-range autoscale, same as the other overlays - a series on the price scale with no "
      "autoscale override of its own is fitted with the candles; otherwise it could run off the pane unnoticed",
      "{color: C.supertrend}));" in draw and "{color: C.supertrend, autoscale" not in draw)
check("the legend names it with its own (length, multiplier), only when a reading exists",
      '(d.supertrend?' in draw.replace(" ", "") and "d.supertrend_len" in draw and "d.supertrend_mult" in draw)

print("5. EVERY THEME DEFINES ITS OWN LINE COLOUR - A FOURTH ONE FORGOTTEN WOULD SHOW css('--supertrend') AS "
      "AN EMPTY STRING (invisible line, not a crash) RATHER THAN FAIL LOUDLY")
check("the chart reads it the same way as every other indicator colour",
      'supertrend: css("--supertrend")' in SRC)
theme_blocks = SRC.count("--supertrend:")
check("all three theme blocks that define --adx also define --supertrend (same count, not fewer)",
      theme_blocks == SRC.count("--adx:") == 3, theme_blocks)

print("6. THE BACKEND CALL SITE USES THE REAL INDEX KEY, NOT THE MARKET STRING "
      "(Bank Nifty's own multiplier would otherwise be silently dropped)")
candles_start = SRC.index("def _candles(self, user, key, qs=None):")
candles = SRC[candles_start:candles_start + 8000]
check("config.supertrend_params(key) - the actual index key already in scope",
      "config.supertrend_params(key)" in candles)
check("NOT self._current_market() - that returns a market string like \"nse_index\", which "
      "config.supertrend_params() would silently fail to find in INSTRUMENTS and fall back to the "
      "global default for every index, Bank Nifty included",
      "config.supertrend_params(self._current_market())" not in candles)
check("the same pin-to-the-card fix ADX got (30 Sep 2026) - the native 15m view's last point matches "
      "whatever actually drove the live trail's most recent check, not a few-minutes-stale recompute",
      'supertrend[-1] = round(float(rec["supertrend"]), 2)' in candles
      and 'tf == "15m"' in candles[candles.index("st_line"):])
check("the array is sent on the chart payload, alongside its own (length, multiplier) for the legend",
      '"supertrend": supertrend, "supertrend_len": st_len, "supertrend_mult": st_mult' in SRC)

print("7. THE TILE'S RENDER LOGIC ITSELF, RUN IN NODE - THE REAL SOURCE EXPRESSION, NOT A COPY OF IT")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if NODE is None:
    check("node is available to run the page's own tile logic", False, "install node")
else:
    esc_src = SRC[SRC.index("const esc=s=>"):SRC.index("const esc=s=>") + 200].split("\n")[0]
    num_src = SRC[SRC.index("const num=(v,d=2)=>"):SRC.index("const esc=s=>")]
    tile_src = SRC[SRC.index("function tile(l,v,d,cls){"):SRC.index("function blank(msg,detail){")]
    # Pull the ACTUAL "Supertrend" tile(...) call out of the Signal card's own
    # tiles block by balanced-paren matching, rather than retyping the ternary
    # by hand - a hand-typed copy would keep passing after someone broke the
    # real source (e.g. swapped which branch gets the up/down colour).
    call_start = tiles_block.index('tile("Supertrend"')
    depth, j = 0, call_start
    while True:
        if tiles_block[j] == "(":
            depth += 1
        elif tiles_block[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    tile_call_expr = tiles_block[call_start:j + 1]
    prog = num_src + esc_src + "\n" + tile_src + f"""
function tileFor(r) {{
  return {tile_call_expr};
}}
""" + r"""
const out = [];
function assertIncludes(html, needle, label){ out.push([label, html.includes(needle)]); }
function assertExcludes(html, needle, label){ out.push([label, !html.includes(needle)]); }

const t1 = tileFor({supertrend: null, spot: 25000});
assertIncludes(t1, ">—<", "a missing reading renders the em dash");
assertExcludes(t1, "var(--up)", "a missing reading has no up colour");
assertExcludes(t1, "var(--down)", "a missing reading has no down colour");

const t2 = tileFor({supertrend: 24950.25, spot: 25000});
assertIncludes(t2, "24,950.25", "a real reading is formatted like every other price (en-IN, 2dp)");
assertIncludes(t2, "var(--up)", "spot above the line reads as the up colour");
assertIncludes(t2, "stop follows this once T1 is touched", "the explanatory sub-text is present");

const t3 = tileFor({supertrend: 25050.0, spot: 25000});
assertIncludes(t3, "var(--down)", "spot below the line reads as the down colour");

console.log(JSON.stringify(out));
"""
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        check("the tile's own render logic runs in node without throwing", False, r.stderr)
    else:
        import json
        for label, ok in json.loads(r.stdout.strip().splitlines()[-1]):
            check(label, ok)

print()
if fails:
    print(f"SUPERTREND DISPLAY TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("SUPERTREND DISPLAY TEST PASSED")
