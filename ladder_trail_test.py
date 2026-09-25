#!/usr/bin/env python3
"""The Signal page's own ladder and risk/reward box - run for real in node -
say so when the stop trails (tickets.py's staircase trailing stop, 22 Sep
2026). Before this, both still described a trade as if the stop never moved
once frozen, which stopped being true the day the trailing stop shipped for
rule tickets too.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own functions", False, "install node to run this section")
    print("LADDER TRAIL TEST FAILED" if fails else "")
    sys.exit(1)

SRC = open(os.path.join(HERE, "web_server.py")).read()
a = SRC.index("const num=")
b = SRC.index("function unitLabel(")
c = SRC.index("function ladder(r, tk){")
d = SRC.index("function riskBox(")
e = SRC.index("function rrBox(r, tk){")
f = SRC.index('$("lb-index").onclick')
h = SRC.index("let CCY = ")
i = SRC.index("function feedTag(")

prog = """
const assert = require("assert");
const EL = {};
function mkEl(id){
  return {id, innerHTML: "", textContent: "", title: "", value: "", disabled: false,
          dataset: {}, options: [], style: {}, classList: {toggle(){}},
          add(opt){ this.options.push(opt); }};
}
function $(id){ return EL[id] || (EL[id] = mkEl(id)); }
class Option { constructor(text, value){ this.text = text; this.value = value; } }
let LAST = null, CUR = "NIFTY";
""" + SRC[h:i] + SRC[a:b] + SRC[b:c] + SRC[c:d] + SRC[e:f] + r'''

// ---- ladder(): AN OPEN RULE TICKET, PAST T1, EXIT AT T2 -------------------
const openT1 = {open: true, tracked_on: "premium", entry: 130.0, now: 150.0,
                strike: 25000, option_type: "CE", exit_at: "T2", lot_size: 65, lots: 1,
                targets: [145.0, 154.0, 160.0], stop: 145.0,
                hit: {T1: true, T2: false, T3: false}, hit_time: {T1: "10:05:00"},
                sl_hit: false, odds: {}};
ladder({}, openT1);
assert.ok($("ladder").innerHTML.includes("Stop · trailed to T1"),
          "an open ticket past T1, not yet at T2 (the exit): the stop rung names T1 - " + $("ladder").innerHTML);
assert.ok($("lnote").innerHTML.includes("moved up to reflect T1"),
          "the note under the ladder explains the stop already moved - " + $("lnote").innerHTML);

// ---- ladder(): NOTHING TRAILED YET - NO STALE OR FALSE CLAIM --------------
const openFresh = Object.assign({}, openT1, {stop: 110.0, hit: {T1: false, T2: false, T3: false}});
ladder({}, openFresh);
assert.ok($("ladder").innerHTML.includes(">Stop<") && !$("ladder").innerHTML.includes("trailed to"),
          "nothing crossed yet: the label reads plain 'Stop', no trailed-to claim - " + $("ladder").innerHTML);
assert.ok(!$("lnote").innerHTML.includes("moved up to reflect"),
          "...and the note says nothing about a move that has not happened");

// ---- ladder(): A SECOND RUNG TRAILS FURTHER, AND THE NOTE NAMES THE LATEST ONLY
const openT2 = Object.assign({}, openT1, {stop: 154.0, hit: {T1: true, T2: true, T3: false}, exit_at: "T3"});
ladder({}, openT2);
assert.ok($("ladder").innerHTML.includes("Stop · trailed to T2") && !$("ladder").innerHTML.includes("trailed to T1"),
          "past both waypoints toward a T3 exit: names the FURTHEST rung reached, not the first - "
          + $("ladder").innerHTML);

// ---- ladder(): NO WAYPOINTS AT ALL WHEN THE EXIT IS T1 ITSELF -------------
const openExitT1 = Object.assign({}, openT1, {exit_at: "T1", hit: {T1: false, T2: false, T3: false}});
ladder({}, openExitT1);
assert.ok(!$("ladder").innerHTML.includes("trailed to"),
          "exit_at T1 means T1 is the exit itself, never a waypoint that can trail the stop - " + $("ladder").innerHTML);
// Even with later rungs marked hit (as a target-hit close would leave them),
// none of them sit BEFORE a T1 exit, so none can be read as a trailed-to rung.
const openExitT1AllHit = Object.assign({}, openT1, {exit_at: "T1", hit: {T1: true, T2: true, T3: true}});
ladder({}, openExitT1AllHit);
assert.ok(!$("ladder").innerHTML.includes("trailed to"),
          "T1, T2 and T3 all hit but exit_at is T1: still no waypoint before the exit - "
          + $("ladder").innerHTML);

// ---- ladder(): THE PRE-TRADE SIGNAL, NO TICKET OPEN YET -------------------
const rec = {ltp: 130.0, premium_targets: [145.0, 154.0, 160.0], premium_stop: 110.0,
             strike: 25000, option_type: "CE", exit_at: "T2", lot_size: 65, premium_source: "live",
             targets: [], odds: {}};
LAST = {market_open: true};
ladder(rec, {open: false});
assert.ok($("lnote").innerHTML.includes("Once a ticket is issued, the stop moves up"),
          "a live suggestion, not yet a ticket: still says the stop WILL trail, forward-looking - "
          + $("lnote").innerHTML);
assert.ok($("lnote").innerHTML.includes("T1"),
          "...and names T1 as the waypoint before a T2 exit - " + $("lnote").innerHTML);

const recExitT1 = Object.assign({}, rec, {exit_at: "T1"});
ladder(recExitT1, {open: false});
assert.ok(!$("lnote").innerHTML.includes("Once a ticket is issued"),
          "a T1 exit has no waypoint before it, so no forward-looking trail note either - "
          + $("lnote").innerHTML);

console.log("ok:ladder");

// ---- rrBox(): THE MAIN SENTENCE ITSELF SAYS THE STOP TRAILS, NOT A FOOTNOTE
const openForRR = {open: true, tracked_on: "premium", entry: 130.0, targets: [145.0, 154.0, 160.0],
                   stop: 145.0, exit_at: "T2", lot_size: 65, lots: 1, odds: {}, charges: null};
rrBox({}, openForRR);
const rrHtml = $("rr").innerHTML;
assert.ok(rrHtml.includes('class="rrsum"') && rrHtml.includes("exits at <b>T2</b>"),
          "still says which target ends the trade - that part is true, T2 IS the exit - " + rrHtml);
const rrSum = rrHtml.slice(rrHtml.indexOf('class="rrsum"'), rrHtml.indexOf("</div>", rrHtml.indexOf('class="rrsum"')));
assert.ok(rrSum.includes("That risk is the worst case, though") && rrSum.includes("<b>T1</b>")
          && rrSum.includes("the stop moves up to it"),
          "...but in the SAME sentence, not a separate footnote: reaching T1 first changes the real risk - " + rrSum);

const liveForRR = {ltp: 130.0, premium_targets: [145.0, 154.0, 160.0], premium_stop: 110.0,
                   bias: "BULLISH", exit_at: "T2", lot_size: 65, odds: {}, charges: null};
rrBox(liveForRR, {open: false});
const rrHtml2 = $("rr").innerHTML;
assert.ok(rrHtml2.includes("That risk is the worst case, though") && rrHtml2.includes("<b>T1</b>"),
          "...and the same, up front, for a live suggestion with no ticket yet - " + rrHtml2);

const t1ExitForRR = Object.assign({}, openForRR, {exit_at: "T1", stop: 110.0});
rrBox({}, t1ExitForRR);
assert.ok(!$("rr").innerHTML.includes("worst case, though"),
          "a T1 exit has no rung before it to trail through, so nothing claims it does - "
          + $("rr").innerHTML);

console.log("ok:rrbox");
'''

r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
out = (r.stdout or "") + (r.stderr or "")
check("the open-ticket ladder names which rung the stop has trailed to, or stays silent when nothing has",
      "ok:ladder" in r.stdout, out[-1500:])
check("the risk/reward box explains the stop is a worst case once a rung can trail it",
      "ok:rrbox" in r.stdout, out[-1500:])
check("the run exits clean", r.returncode == 0, out[-800:])

print("LADDER TRAIL TEST PASSED" if not fails else f"LADDER TRAIL TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
