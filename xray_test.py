#!/usr/bin/env python3
"""The Market X-Ray (the user, 27 Oct 2026, after order-block.com's front page: "threads coming out of sensex and going
through all the market and showing what is going up and what is going down and the thread should go from our votes and
then the signal fires" ... "same like in the website"). Display only - the index's own stocks (the market map's prices),
then the votes and checks the ticket reads (gateSteps / gateStage), then the signal. The page's own functions run in node
on readings shaped like /api/state's. No network."""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

src = open(os.path.join(HERE, "web_server.py")).read()
a = src.index("const XR = {")
z = src.index("function sigMovers(){")
xr = src[a:z]

print("1. WHERE IT IS DRAWN")
sig = src[src.index('<section class="pane" data-pane="signal">'):src.index("</section>", src.index('data-pane="signal">'))]
home = src[src.index('<section class="pane on" data-pane="home">'):src.index("</section>", src.index('data-pane="home"'))]
check("on the Signal page, under the signal card", 'id="xraycard"' in sig and 'id="xrsigbox"' in sig and 'id="xrsigsay"' in sig
      and sig.index('id="xraycard"') > sig.index('id="sigcard"'))
check("and on the Dashboard (the main screen), its own slot outside #kdash so phones get it too",
      '<div class="xrdash" id="xrdash" hidden></div>' in home and home.index('id="xrdash"') > home.index('id="kdash"'))
check("drawn with every reading, on both, and a failure there cannot stop the page",
      src.count("try{ xrayAll(s); }catch(e){}") == 2
      and "try{ xrayAll(s); }catch(e){}" in src[src.index("function homeDraw(s){"):src.index("\n}\n", src.index("function homeDraw(s){"))])
check("the words under it are read out when they change (role=status)", 'id="xrsigsay" role="status"' in sig
      and 'data-xr-say role="status"' in xr)

print("2. DISPLAY ONLY")
check("no fetch, no order, no setting of its own - it reads LAST / MAPDATA and the Gate tracks' own checks",
      "fetch(" not in xr and "/api/" not in xr and "POST" not in xr and "gateSteps(r, ts)" in xr and "gateStage(r, ts, g)" in xr)
check("the canvas is hidden from screen readers - the sentence under it says the same",
      'canvas.setAttribute("aria-hidden", "true")' in xr)
check("the stocks are said to be context, not a vote", "shown, not a vote" in xr)

print("3. MOTION")
check("still under prefers-reduced-motion: no loop, the still layer only",
      'matchMedia("(prefers-reduced-motion: reduce)")' in xr and "if(XR.still || XR.raf) return;" in xr
      and "if(XR.still || t == null) return;" in xr)
check("the loop runs only while a box is on screen and the tab is shown, about 30 a second",
      "x.seen && x.static" in xr and "document.hidden" in xr and "t - XR.last >= 33" in xr)
check("laid out again when the box changes size or the scheme changes",
      "new ResizeObserver(" in xr and "document.documentElement.dataset.scheme" in xr and "document.documentElement.dataset.skin" in xr)
check("the additive glow only on a dark card (it adds up to white on a light one)",
      'x.glow ? "lighter" : "source-over"' in xr)
check("top to bottom on a narrow box (a phone), left to right on a wide one; a tall box on phones",
      "const tall = W < 560;" in xr and re.search(r"@media \(max-width:600px\)\{[^}]*\.xr-box\{height:560px\}", src))

print("4. THE MODEL (node)")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
else:
    g0 = src.index("const GT_SESSION =")
    g1 = src.index("let GT_HTML")
    m0 = src.index("function xrModel(")
    m1 = src.index("function xrInst(")
    u0 = src.index("function xrUpdate(")
    u1 = src.index("function xrayAll(")
    base = dict(spot=73598.7, bias="NEUTRAL", agree=2, dissent=1, adx=24.0, adx_needed=20, adx_blocked=False,
                not_worth_it=False, macd_blocked=False, reach_points=140.0, ticket_gate_ratio=1.4, ticket_gate_need=1.0,
                trend={"day_change_pct": 0.16})
    votes = [{"name": "Trend", "vote": 1}, {"name": "MACD", "vote": 1}, {"name": "RSI", "vote": -1}, {"name": "PCR", "vote": None}]
    tiles = [{"sym": "A", "pct": 1.2}, {"sym": "B", "pct": -2.5}, {"sym": "C", "pct": 0.4}, {"sym": "D", "pct": None},
             {"sym": "E", "pct": 3.1}, {"sym": "F", "pct": 0}]
    prog = r'''
const assert = require("assert");
const esc = s => String(s == null ? "" : s).replace(/[&<>]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]));
const num = (v, d = 2) => v == null || isNaN(v) ? "—" : Number(v).toFixed(d);
const money = v => (v >= 0 ? "+" : "-") + "Rs" + Math.abs(Math.round(v));
global.matchMedia = () => ({matches: false});
global.document = {documentElement: {dataset: {scheme: "dark", skin: "terminal"}}};
let CUR = "SENSEX", MAPDATA = {index: "SENSEX", tiles: ''' + json.dumps(tiles) + r'''};
''' + src[g0:g1] + src[a:src.index("function xrModel(")] + src[m0:m1] + src[u0:u1] + r'''
const B = ''' + json.dumps(base) + r''', V = ''' + json.dumps(votes) + r''';
const st = (r, ts) => ({indices: {SENSEX: r}, tickets: {SENSEX: ts}, why: {SENSEX: {votes: V}}});
const say = m => { const x = {box: {clientWidth: 0, clientHeight: 0}, say: {textContent: ""}, title: {textContent: ""}};
                   xrUpdate(x, m); return [x.say.textContent, x.title.textContent]; };
// watching: the stocks sorted by today's move, no-price ones left out; the votes then ADX / Room / R:R
let m = xrModel(st(B, {ticket: null, wait: {code: "neutral", badge: "NO SIGNAL"}}));
assert.deepStrictEqual(m.stocks.map(s => s.n), ["E", "A", "C", "F", "B"]);
assert.strictEqual(m.up, 3); assert.strictEqual(m.down, 1);
assert.deepStrictEqual(m.checks.map(c => c.n + ":" + c.v), ["Trend:1", "MACD:1", "RSI:-1", "PCR:0", "ADX:2", "Room:2", "R:R:2"]);
assert.strictEqual(m.fired, false); assert.strictEqual(m.word, "NO TRADE"); assert.strictEqual(m.side, 0);
let [t, ti] = say(m);
assert.ok(t.startsWith("3 of 5 of SENSEX's own stocks up, 1 down - shown, not a vote. No trade yet - "), t);
assert.ok(!t.includes(". .") && t.endsWith("."), t); assert.strictEqual(ti, "SENSEX");
console.log("ok:watch");
// every check passes on a bearish lean: fired, BUY PE
m = xrModel(st(Object.assign({}, B, {bias: "BEARISH"}), {ticket: null, wait: null}));
assert.strictEqual(m.fired, true); assert.strictEqual(m.word, "BUY PE"); assert.strictEqual(m.side, -1);
assert.ok(say(m)[0].endsWith("Signal: BUY PE."), say(m)[0]);
console.log("ok:ready");
// an open ticket: in trade, its strike, the side from the option
m = xrModel(st(Object.assign({}, B, {bias: "BEARISH"}), {ticket: {open: true, strike: 73600, option_type: "CE", pnl: 900},
                                                         wait: {code: "position_open"}}));
assert.strictEqual(m.fired, true); assert.strictEqual(m.word, "IN TRADE · 73600 CE"); assert.strictEqual(m.side, 1);
console.log("ok:trade");
// a rule index (Trend Rider, TradingView): its own conditions are the checks, met / not met
m = xrModel(st(Object.assign({}, B, {rule: {system: "tv_rating", label: "TradingView rating",
              filters: [{name: "TradingView overall rating", ok: false}, {name: "ADX rising", ok: true}, {name: "x", ok: null},
                        {name: "Volume surge: 1.5x the 20-day average", ok: true}]}}),
              {ticket: null, wait: {code: "neutral"}}));
assert.deepStrictEqual(m.checks.map(c => c.n + ":" + c.v),
                       ["TV overall rating:-2", "ADX rising:2", "x:0", "Volume surge: 1.5…:2"], "a long name ends in an ellipsis");
console.log("ok:rule");
// another index's map on hand (the user just switched): no stocks drawn rather than the wrong ones
MAPDATA = {index: "NIFTY", tiles: [{sym: "Z", pct: 1}]};
m = xrModel(st(B, {ticket: null, wait: {code: "neutral"}}));
assert.strictEqual(m.stocks.length, 0); assert.ok(say(m)[0].startsWith("No trade yet"), say(m)[0]);
assert.strictEqual(xrModel({indices: {}}), null, "no reading for the index: nothing");
console.log("ok:switch");
'''
    rr = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = rr.stdout + rr.stderr
    for tag, name in (("watch", "watching: stocks sorted by move (no-price left out), votes then ADX/Room/R:R, NO TRADE and why"),
                      ("ready", "every check passes on a bearish lean: fired, BUY PE"),
                      ("trade", "an open ticket: IN TRADE with its strike, side from the option"),
                      ("rule", "a rule index: its own conditions are the checks"),
                      ("switch", "another index's map on hand: no stocks rather than the wrong ones")):
        check(name, f"ok:{tag}" in out, out[-700:] if f"ok:{tag}" not in out else "")

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
