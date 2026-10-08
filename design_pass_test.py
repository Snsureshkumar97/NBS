#!/usr/bin/env python3
"""The 8 Oct 2026 design pass's additions (the user: "go through all the tab in the tool and work on designing the layout
more presentable and informative ... if you want to add something anywhere like new feature or anything usefull for the
tool you can"). Runs the page's own code in node where it can:

  - Volatility: each cone window drawn as a range bar on one shared scale - the middle half a band, the median a tick,
    now a dot coloured by its percentile.
  - Levels: the price now and the pair of floor pivots it sits between; and the pivots built from the last FINISHED
    session (during market hours the broker's daily candles end with today's, still forming).
  - Seasonality: today's weekday marked; each average a bar either side of zero, on the scale of a day's ordinary move.
  - Signal: a Trend Rider ticket's stop ladder - where each step fires and where it moves the stop.
  - Ask TradePicker: starter questions in the empty conversation; the watchlist's empty state leads to the chain."""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "web_server.py")).read()

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

def fn(name):
    a = SRC.index(f"function {name}(")
    return SRC[a:SRC.index("\n}\n", a) + 3]

def between(start, end):
    a = SRC.index(start)
    return SRC[a:SRC.index(end, a) + len(end)]

print("1. THE SOURCE")
check("Levels: today's still-forming daily candle is dropped before the pivots are built",
      "d.index[-1].date() == ist_now.date()" in SRC and "(ist_now.hour, ist_now.minute) < (15, 30)" in SRC
      and "d = d.iloc[:-1]" in SRC)
check("...and each row carries the price now", 'row["now"] = round(' in SRC)
check("the Signal card lists the ticket's stop steps", "for(const sp of (tk.stop_steps || []))" in SRC)
check("Ask TradePicker: four starter questions, hidden once a message is written, back on a new chat",
      SRC.count('<div class="botstart" id="botstart"') == 1
      and len(re.findall(r'<div class="botstart"[\s\S]*?</div>', SRC)[0].split("<button")) == 5
      and 'if(role !== "sys"){ const bs = $("botstart"); if(bs) bs.hidden = true; }' in SRC
      and SRC.count('const bs = $("botstart"); if(bs) bs.hidden = false;') == 2)
check("the empty watchlist has a button to the option chain", "onclick=\"showTab('chain')\">Open the option chain</button>" in SRC)
check("the footer no longer says crypto figures are before Delta Exchange's fees",
      "before Delta Exchange&rsquo;s fees" not in SRC and "after the Exness spread" in SRC)
check("no text under 12px in the new styles",
      not [x for x in re.findall(r"(?:\.botstart|\.todaytag|td\.rbar|td\.dbar|td\.zone)[^{]*\{[^}]*font-size:(\d+)px", SRC)
           if int(x) < 12])

print("2. THE PAGE'S OWN CODE, IN NODE")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False, "install node to run this section")
else:
    lines = SRC.splitlines()
    pick = lambda start: next(l for l in lines if l.startswith(start))
    head = "\n".join([pick("const esc=s=>"), pick("const num=(v,d=2)=>"), lines[lines.index(pick("const num=(v,d=2)=>")) + 1]])
    zone = between("  const zone = r => {", "\n  };")
    cone = between("  const crows = cone.rows || [];", 'const rankCol = r => r.rank < 20 ? "var(--up)" : r.rank > 80 ? "var(--down)" : "var(--ink)";')
    conebar = between('     ["Range", r => `<td class="rbar"', ', "rbar"],')
    prog = head + "\n" + fn("scrTable") + "\n" + zone + r'''
const assert = require("assert");
const T = {};
const $ = id => (T[id] = T[id] || {innerHTML: ""});
// scrTable: a header class and a class per row
scrTable("t", [{a: 1}, {a: 2}], [["A", r => `<td>${r.a}</td>`, "rbar"]], "none", r => r.a === 2 ? "today" : "");
assert.ok(T.t.innerHTML.includes('<th class="rbar">A</th>') && T.t.innerHTML.includes('<tr><td>1</td></tr>')
          && T.t.innerHTML.includes('<tr class="today"><td>2</td></tr>'), T.t.innerHTML);
// the zone a price sits in
const L = {s2: 100, s1: 110, pivot: 120, r1: 130, r2: 140};
const z = now => zone(Object.assign({now}, L));
assert.deepStrictEqual(z(95), ["below S2", "var(--down)"]);
assert.deepStrictEqual(z(105), ["S2 – S1", "var(--down)"]);
assert.deepStrictEqual(z(115), ["S1 – P", "var(--down)"]);
assert.deepStrictEqual(z(120), ["P – R1", "var(--ink-2)"], "on the pivot: neither side");
assert.deepStrictEqual(z(125), ["P – R1", "var(--up)"]);
assert.deepStrictEqual(z(135), ["R1 – R2", "var(--up)"]);
assert.deepStrictEqual(z(140), ["above R2", "var(--up)"]);
assert.deepStrictEqual(zone(Object.assign({now: null}, L)), ["—", "var(--ink-3)"]);
// the cone: one scale across all windows, the dot where now sits, coloured by percentile
const cone = {rows: [{window: 5, now: 21, min: 2, p25: 5, median: 10, p75: 15, max: 40, rank: 90, n: 300},
                     {window: 90, now: 12, min: 8, p25: 10, median: 11, p75: 12, max: 16, rank: 10, n: 300}]};
''' + cone + r'''
const bar = r => ''' + conebar.split(", r =>", 1)[1].rsplit(", \"rbar\"]", 1)[0].rstrip("]") + r''';
const b5 = bar(cone.rows[0]), b90 = bar(cone.rows[1]);
assert.ok(b5.includes('class="rb-rng" style="left:0.0%;right:calc(100% - 100.0%)"'), "the widest window spans the scale: " + b5);
assert.ok(b5.includes('class="rb-now" style="left:50.0%;background:var(--down)"'), "now at 21 of 2-40, rank 90: red: " + b5);
assert.ok(b90.includes('class="rb-rng" style="left:15.8%;right:calc(100% - 36.8%)"'), "a narrow window, on the same scale: " + b90);
assert.ok(b90.includes('class="rb-now" style="left:26.3%;background:var(--up)"') && b90.includes('class="rb-med" style="left:23.7%"'), b90);
console.log("ok:pieces");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("scrTable's header and row classes; the pivot zone at every band; the cone on one shared scale",
          "ok:pieces" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-900:])

    # the Signal card's ticket panel, drawn with a Trend Rider ticket
    body = between("  // The position panel. Entry and Now are the two numbers a held position is",
                   '  } else st.style.display = "none";')
    prog = "\n".join([head, 'let CCY = "INR";', fn("ccySym"), fn("ccyLocale"), fn("money"), fn("posPanel")]) + r'''
const assert = require("assert");
const entryLabel = t => "Entry";
const ST = {style: {}, innerHTML: ""};
const $ = id => ST;
function draw(tk, r, LAST){
  const open = true;
''' + body + r'''
  return ST.innerHTML;
}
const tk = {index: "NIFTY", tracked_on: "premium", entry: 90, now: 109, pnl: 1235, lots: 1, lot_size: 65, system: "trend_rider",
            stop_steps: [{near: "T2", to: "T1", at: 108, stop: 100, done: true}, {near: "T3", to: "T2", at: 117, stop: 110, done: false}]};
let h = draw(tk, {spot: 25210}, {});
assert.ok(h.includes('<span>Near T2</span><b style="color:var(--up)">stop moved to T1 · 100.00</b>'), "a fired step: " + h);
assert.ok(h.includes("<span>Near T3</span><b>at 117.00 the stop goes to T2 · 110.00</b>"), "a waiting step: " + h);
h = draw(Object.assign({}, tk, {stop_steps: [], system: "rules"}), {spot: 25210}, {});
assert.ok(!h.includes("Near T"), "a rules ticket shows no ladder: " + h);
console.log("ok:ladder");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("the Signal card: a fired step in green ('stop moved to T1'), a waiting one with its trigger; none for rules",
          "ok:ladder" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-900:])

print()
print("DESIGN PASS TEST PASSED" if not fails else f"DESIGN PASS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
