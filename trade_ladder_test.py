#!/usr/bin/env python3
"""The option chain's sellers / support drawing beside the trade (the user, 22 Oct 2026: "the one which we added in the
option chain can we add beside the trade ... when the tool enters the trade can the diagram show that particular strike
and explain how much it needs to cross and give explanation in simple words"). One drawing for the Option chain tab and
the Signal page (web_server ladderDraw): the open ticket's strike ringed, its target and stop as lines, labels that never
sit under a line or on each other, built at the box's own width; the trade in plain words (tradeSay); the Signal page's
layout. No network."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import feeds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra if not cond else ''}")
    if not cond:
        fails.append(name)


L = 1e5
SPOT = 23112.0
CALL = {23150: 44.7, 23200: 92.7, 23300: 65.9}
PUT = {23000: 100.7, 22800: 50.4, 22900: 30.0}
strikes = []
for k in range(22600, 23650, 50):
    strikes.append({"strike": float(k), "call_oi": CALL.get(k, 20.0 if k > SPOT else 10.0) * L,
                    "put_oi": PUT.get(k, 20.0 if k < SPOT else 8.0) * L})
ADDED = {23150: 14.7, 23200: 12.8}                       # calls written in the last 15 minutes: 'sellers adding'
past = {s["strike"]: (s["call_oi"] - ADDED.get(int(s["strike"]), 0) * L, s["put_oi"]) for s in strikes}
SE = feeds._sellers_ahead({"strikes": strikes}, SPOT, past, 15 * 60)
ROWS = [{"strike": s["strike"], "ce": {"oi": s["call_oi"]}, "pe": {"oi": s["put_oi"]}} for s in strikes]


def ticket(side, strike, entry, targets, stop, exit_at="T3", **kw):
    t = {"index": "NIFTY", "strike": strike, "option_type": side, "open": True, "status": "OPEN", "cfd": False,
         "index_targets": targets, "index_stop": stop, "entry_spot": entry, "exit_at": exit_at}
    t.update(kw)
    return {"ticket": t, "wait": None}


CE = ticket("CE", 23100, 23090, [23160, 23230, 23290], 23040)
CE_LOW_STOP = ticket("CE", 23100, 23090, [23160, 23230, 23290], 22980)
CE_T1 = ticket("CE", 23100, 23090, [23160, 23230, 23290], 23040, exit_at="T1")
PE = ticket("PE", 23100, 23130, [23050, 22990, 22930], 23180)
PAST = ticket("CE", 23000, 22990, [23040, 23070, 23100], 22950)
FAR = ticket("CE", 23100, 23090, [23300, 23600, 23900], 23040)

src = open(os.path.join(HERE, "web_server.py")).read()
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
    print("TRADE LADDER TEST FAILED")
    sys.exit(1)
n0 = src.index("const num=(v,d=2)")
n1 = src.index("\n", src.index("const esc=s=>", n0)) + 1
code = (src[n0:n1] + src[src.index("function sellersSay(se){"):src.index("function sellersRows(se){")]
        + src[src.index("const LADDER_EXIT"):src.index("function chainDraw(d){")])
cases = {"SE": SE, "ROWS": ROWS, "SPOT": SPOT, "CE": CE, "CE_LOW_STOP": CE_LOW_STOP, "CE_T1": CE_T1, "PE": PE, "PAST": PAST,
         "FAR": FAR}
prog = code + "\nconst X = " + json.dumps(cases) + r''';
const getComputedStyle = () => ({});
const d = {index: "NIFTY", spot: X.SPOT, rows: X.ROWS};
const tr = t => ladderTrade(t, "NIFTY");
const o = {};
o.trade = {ce: tr(X.CE), t1: tr(X.CE_T1), pe: tr(X.PE),
           closed: tr({ticket: Object.assign({}, X.CE.ticket, {open: false})}),
           cfd: tr({ticket: Object.assign({}, X.CE.ticket, {cfd: true})}),
           other: ladderTrade(X.CE, "SENSEX"), none: tr(null),
           t2: tr({ticket: Object.assign({}, X.CE.ticket, {exit_at: "T2"})}),
           noexit: tr({ticket: Object.assign({}, X.CE.ticket, {exit_at: undefined})})};
o.rows = {plain: ladderRows(d, X.SPOT, null).map(r => r.strike), pe: ladderRows(d, X.SPOT, tr(X.PE)).map(r => r.strike),
          low: ladderRows(d, X.SPOT, tr({ticket: Object.assign({}, X.PE.ticket, {index_targets: [23050, 22800, 22630]})})).map(r => r.strike),
          far: ladderRows(d, X.SPOT, tr(X.FAR)).map(r => r.strike)};
o.say = {};
for (const k of ["CE", "CE_LOW_STOP", "CE_T1", "PE", "PAST"]) {
  const t = tr(X[k]);
  o.say[k] = tradeSay(t, X.SPOT, ladderRows(d, X.SPOT, t), X.SE, "NIFTY");
}
o.say.none = tradeSay(null, X.SPOT, [], X.SE, "NIFTY");
o.svg = {ce: ladderDraw(d, X.SE, tr(X.CE), 680).svg, pe: ladderDraw(d, X.SE, tr(X.PE), 680).svg,
         plain: ladderDraw(d, X.SE, null, 680).svg, narrow: ladderDraw(d, X.SE, tr(X.CE), 340).svg,
         far: ladderDraw(d, X.SE, tr(X.FAR), 680).svg, wide: ladderDraw(d, X.SE, null, 2000).svg};
o.moving = [23090, 23112, 23140, 23175].map(v => ladderDraw(d, X.SE, tr(X.CE), 680, v));
console.log(JSON.stringify(o));
'''
r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
try:
    o = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    print(r.stdout[-2000:], r.stderr[-2000:])
    o = {}
tr, rows, say, svg = o.get("trade", {}), o.get("rows", {}), o.get("say", {}), o.get("svg", {})

print("1. WHICH TRADE IS DRAWN")
ce = tr.get("ce") or {}
check("an open call: up, its strike, target = the T3 it exits at, the stop and the entry, in index points",
      ce == {"up": True, "type": "CE", "strike": 23100, "target": 23290, "stop": 23040, "entry": 23090, "exit_at": "T3"}, ce)
check("a T1 exit: the target is T1, a T2 exit T2; no exit named: the last target",
      (tr.get("t1") or {}).get("target") == 23160 and (tr.get("t2") or {}).get("target") == 23230
      and (tr.get("noexit") or {}).get("target") == 23290 and (tr.get("noexit") or {}).get("exit_at") == "T3")
check("a put: down", (tr.get("pe") or {}).get("up") is False)
check("no trade drawn: a closed ticket, a CFD (no strike, no chain), another index's, none",
      tr.get("closed") is None and tr.get("cfd") is None and tr.get("other") is None and tr.get("none") is None)

print("2. THE STRIKES SHOWN")
pl = rows.get("plain", [])
check("no trade: the strikes within 1.5% of the price, highest at the top (as before)",
      pl and pl == sorted(pl, reverse=True) and all(abs(k - SPOT) <= SPOT * 0.015 for k in pl), pl)
pe = rows.get("pe", [])
check("a put whose levels are inside the 1.5% strikes: the same strikes as with no trade", pe == pl, (pe[:2], pe[-2:]))
lowt = rows.get("low", [])
check("a target below them: down to the strike at or under it, no further", lowt and lowt[-1] == 22600 and lowt[0] == pl[0], lowt[-3:])
far = rows.get("far", [])
check("a target past the chain: at most 30 strikes kept", 0 < len(far) <= 30, len(far))

print("3. THE TRADE IN PLAIN WORDS")
c = say.get("CE", "")
check("the strike against the price: the call is in the money",
      c.startswith("This trade holds the 23,100 call (CE), bought when Nifty was at 23,090. Nifty is 12 points above the strike now, so the call is in the money."), c)
check("how far the target is: 23,290 (T3), another 178 points up",
      "To reach the target at 23,290 (T3), Nifty has to rise another 178 points." in c, c)
check("the sellers in the way, named with their size and distance, and that more are being added",
      "On the way, sellers are waiting at 23,200 (92.7 lakh calls written), 88 points above the price: it has to get past that level first." in c
      and "More are being added right now, so that level is getting stronger." in c, c)
check("23,300 is past the target, so it is not 'in the way'", "23,300" not in c, c)
check("the stop: where and how far", c.endswith("The stop is at 23,040, 72 points below the price."), c)
lo = say.get("CE_LOW_STOP", "")
check("support between the price and a lower stop is named", "Before it, support at 23,000 (100.7 lakh puts written) can slow a fall on the way to the stop." in lo, lo)
t1 = say.get("CE_T1", "")
check("no big sellers before a near target: said so (23,150's 44.7 lakh is under 60% of the heaviest)",
      "No big group of call sellers stands between the price and the target." in t1 and "(T1)" in t1, t1)
p = say.get("PE", "")
check("a put: out of the money, it has to fall past the strike",
      "This trade holds the 23,100 put (PE), bought when Nifty was at 23,130. Nifty is 12 points above the strike now: it has to fall past 23,100 for the put to be in the money." in p, p)
check("a put's way down: the support it has to get past",
      "To reach the target at 22,930 (T3), Nifty has to fall another 182 points. On the way, support is waiting at 23,000 (100.7 lakh puts written), 112 points below the price: it has to get past that level first." in p, p)
check("a put's stop above", p.endswith("The stop is at 23,180, 68 points above the price."), p)
check("already past the target: said so", "Nifty is already past the target at 23,100." in say.get("PAST", ""), say.get("PAST"))
check("no trade: nothing said", say.get("none") == "")
check("never a call to buy or sell", not any(w in json.dumps(say).lower() for w in ("buy now", "sell now", "you should", "exit now")))

print("4. THE DRAWING")
s_ce = svg.get("ce", "")
check("the trade's strike ringed, and its number marked", s_ce.count('class="ocl-mine"') == 1 and 'class="ocl-k mine">23,100' in s_ce
      or ('class="ocl-k on mine">23,100' in s_ce), re.findall(r'class="ocl-k[^"]*">23,100', s_ce))
check("the target and the stop as lines, labelled", 'class="ocl-tl tgt"' in s_ce and 'class="ocl-tl stp"' in s_ce
      and ">target 23,290<" in s_ce and ">stop 23,040<" in s_ce)
check("a call's labels on the puts' side (right), the price's on the left",
      'x="674" y="' in s_ce and re.search(r'<text x="674" y="[\d.]+" text-anchor="end" class="ocl-tlab tgt">target', s_ce)
      and re.search(r'<text x="6" y="[\d.]+" text-anchor="start" class="ocl-spotl">price 23,112', s_ce))
s_pe = svg.get("pe", "")
check("a put's labels on the calls' side (left)", re.search(r'<text x="6" y="[\d.]+" text-anchor="start" class="ocl-tlab tgt">target 22,930', s_pe)
      and re.search(r'<text x="674" y="[\d.]+" text-anchor="end" class="ocl-spotl">price', s_pe))
check("layers: every line before every label, so a line never strikes through one", s_ce.index("<line") < s_ce.index("<text"))
check("every strike and label sits on a background of its own", s_ce.count('class="ocl-bg"') == s_ce.count("<text") - 3)
# the put's stop at 23,180 would sit on the '92.7 L' sellers label at 23,200: it moves under its line
m = re.search(r'<line x1="4" x2="676" y1="([\d.]+)" y2="[\d.]+" class="ocl-tl stp"/>', s_pe)
ms = re.search(r'<text x="6" y="([\d.]+)" text-anchor="start" class="ocl-tlab stp">stop 23,180', s_pe)
check("a line's label that would cover another label moves under its line", m and ms and abs(float(ms.group(1)) - float(m.group(1)) - 14) < 0.01,
      (m and m.group(1), ms and ms.group(1)))
pl_ = svg.get("plain", "")
check("no trade: no ring, no trade lines, the price labelled on the right as before",
      "ocl-mine" not in pl_ and "ocl-tl" not in pl_ and re.search(r'<text x="674" y="[\d.]+" text-anchor="end" class="ocl-spotl">price', pl_))
nr = svg.get("narrow", "")
check("built at the box's width (a phone: 340), the labels short so they fit", 'width="340" height=' in nr
      and "← sellers (calls)" in nr and ">92.7 L +12.8<" in nr and ">sellers 92.7" not in nr)
check("never wider than 760", 'width="760" height=' in svg.get("wide", ""))
fr = svg.get("far", "")
check("a target beyond the strikes shown: its line pinned to the edge, dotted, with an arrow", 'class="ocl-tl tgt off"' in fr and "target 23,900 ↑" in fr, fr[-400:])
check("12px text everywhere: the drawing is not scaled (its width is set, the box only caps it)",
      ".ocse svg.ocl,.sl-draw svg.ocl{display:block;max-width:100%;height:auto}" in src and "width:100%;max-width:760px" not in src)

print("5. THE PRICE LINE FOLLOWS THE LIVE PRICE (\"does it also move as the index price change ... like a ladder\")")
fn = src[src.index("function ladderSpot(d){"):src.index("let OC_LAST = null;")]
prog3 = (fn + r'''
const out = {};
out.none = ladderSpot({index: "NIFTY", spot: 23112});
var LAST = {indices: {NIFTY: {spot: 23161.5}}};
out.live = ladderSpot({index: "NIFTY", spot: 23112});
out.other = ladderSpot({index: "SENSEX", spot: 76000});
LAST.indices.NIFTY.spot = null;
out.nolive = ladderSpot({index: "NIFTY", spot: 23112});
console.log(JSON.stringify(out));''')
r3 = subprocess.run([NODE, "-e", prog3], capture_output=True, text=True, timeout=60)
try:
    o3 = json.loads(r3.stdout.strip().splitlines()[-1])
except Exception:
    print(r3.stdout, r3.stderr)
    o3 = {}
check("the live index price when the page has one, the chain's own otherwise",
      o3 == {"none": 23112, "live": 23161.5, "other": 76000, "nolive": 23112}, o3)
check("both drawings use it; the Signal one is redrawn by every price update (render), the Option chain's too",
      src.count("ladderDraw(d, se, tr, w > 0 ? w : 680, ladderSpot(d), ") == 2 and "  sigSellers(r);\n  try{ if(TAB === \"chain\" && OC_LAST && OC_LAST.index === CUR) ocSellersDraw(OC_LAST); }catch(e){}" in src
      and "if(changed) render(LAST);" in src)

mv = o.get("moving", [])
ys = [float(re.search(r'y1="([\d.]+)" y2="[\d.]+" class="ocl-spot"', m["svg"]).group(1)) for m in mv] if mv else []
ks = [re.findall(r'class="ocl-k[^"]*">([\d,]+)<', m["svg"]) for m in mv]
check("as the price rises 23,090 -> 23,175 the line climbs, on strikes that stay put",
      len(ys) == 4 and ys == sorted(ys, reverse=True) and len(set(ys)) == 4 and all(k == ks[0] for k in ks), (ys, [len(k) for k in ks]))
check("and the words follow the live price", mv and "another 200 points" in mv[0]["tsay"] and "another 115 points" in mv[3]["tsay"],
      [m["tsay"][150:260] for m in mv[::3]])

print("6. ON THE PAGE")
sig = src[src.index('<section class="pane" data-pane="signal">'):src.index("</section>", src.index('data-pane="signal">'))]
st = sig.index('<div class="sigtrade" id="sigtrade">')
order = [sig.index(x, st) for x in ('id="ladder"', 'id="ladabout"', 'id="rr"', 'id="sigsellers"')]
check("beside the trade: the targets ladder, its notes and the risk card on the left, the drawing on the right",
      order == sorted(order) and sig.count('id="sigsellers"') == 1 and order[-1] < sig.index('id="risk"'))
check("side by side when the card is wide enough for both, one under the other otherwise and on a phone",
      '@container sigcard (min-width:720px){\n  #sigcard:not([data-state="blank"]) .sigtrade:has(> #sigsellers:not(:empty)){display:grid;' in src
      and "#sigcard{container:sigcard / inline-size}" in src
      and ':root[data-look="kite"] #sigtrade{order:10}' in src)
check("the Signal page reads the same chain as the Option chain tab, every 20 seconds while it shows",
      "SIGCHAIN = {for: want, at: Date.now(), d};   // the Signal page's drawing reads the same chain" in src
      and 'TAB === "signal" && (SIGCHAIN.for !== CUR || Date.now() - SIGCHAIN.at > 20000)' in src)
check("until the chain comes: the levels as the short table", "set(wd, sellersRows(se));" in src)
check("the header names the ringed strike, the note says what the lines are",
      "your ${num(tr.strike, 0)} ${tr.type} ringed" in src and "The ringed strike is the one this trade holds; the green line is its" in src)
check("the note sits in a fold a redraw does not close (only the drawing and the words are redrawn)",
      '<details class="about sl-about"><summary>What the drawing shows</summary>' in src and "const set = (x, h) => { if(x._h !== h)" in src)
check("redrawn when the box changes width (a phone turned)", "new ResizeObserver(" in src and "el._redraw()" in src)
check("the Option chain tab's drawing marks the trade too", "const tr = ladderTrade(((LAST && LAST.tickets) || {})[d.index], d.index);" in src)

print()
print("TRADE LADDER TEST PASSED" if not fails else f"TRADE LADDER TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
