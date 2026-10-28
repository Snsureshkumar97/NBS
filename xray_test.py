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

print("1. WHERE IT IS DRAWN - A PAGE OF ITS OWN (\"give me separate tab so it can have lot of space\")")
pane = src[src.index('<section class="pane" data-pane="xray">'):src.index("</section>", src.index('data-pane="xray">'))]
sig = src[src.index('<section class="pane" data-pane="signal">'):src.index("</section>", src.index('data-pane="signal">'))]
home = src[src.index('<section class="pane on" data-pane="home">'):src.index("</section>", src.index('data-pane="home"'))]
check("its own page: the box, the words under it, the index buttons and what it shows",
      'id="xrsigbox"' in pane and 'id="xrsigsay" role="status"' in pane and 'id="xridx"' in pane and "shown, not a vote" in pane)
check("reached from the Market menu, named, drawn when opened and with every reading - only while it is on screen",
      'data-tab="xray"' in src and '"gates", "xray"];' in src and 'xray:"Market X-Ray"' in src
      and 'if(name === "xray") xrayAll(LAST);' in src and 'if(TAB !== "xray") return;' in xr
      and src.count("try{ xrayAll(s); }catch(e){}") == 1)
check("no longer squeezed onto the Signal page or the Dashboard - the Dashboard keeps a tile to it",
      "xraycard" not in sig and "xr-box" not in home and "xrdash" not in src and '["xray", "<svg' in src)
check("hidden on the crypto screen: a coin or gold has no stocks of its own", '["xray", crypto]' in src)
check("the index buttons are the watchlist's own list and the same switch", "s.order || Object.keys(s.indices)" in xr
      and "selectIndex(b.dataset.k)" in xr and 'aria-pressed="${k === CUR}"' in xr)

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
check("top to bottom on a narrow box (a phone), left to right on a wide one; the box as tall as its stocks need",
      "const tall = W < XR_WIDE;" in xr and "XR_WIDE = 560" in xr and "xrFit(x, m);" in xr)
check("on a phone the longest name sets a size that fits every name inside the box", "measureText(words(q))" in xr)
check("each check's node is green with a tick when it passes, red with a cross when not, grey with a dash - drawn as lines",
      "const st = xrPass(c, m.side);" in xr and "st === 1 ? col.up : st === -1 ? col.down : st === 0 ? col.ink3" in xr
      and "mark(q);" in xr and "fillText" not in xr[xr.index("const mark = q =>"):xr.index("mark(q);")])

print("4. THE MODEL (node)")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
else:
    g0 = src.index("const GT_SESSION =")
    g1 = src.index("let GT_HTML")
    m0 = src.index("function xrModel(")
    m1 = src.index("function xrInst(")
    c0 = src.index("function xrCols(")
    c1 = src.index("function xrBez(")
    u0 = src.index("function xrUpdate(")
    u1 = src.index("// the indices to look at")
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
''' + src[g0:g1] + src[a:src.index("function xrModel(")] + src[m0:m1] + src[c0:c1] + src[u0:u1] + r'''
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
assert.ok(t.startsWith("3 of 5 of SENSEX's own stocks up, 1 down - shown, not a vote. No trade - "), t);
assert.ok(!t.includes(". .") && t.endsWith("."), t); assert.strictEqual(ti, "SENSEX");
console.log("ok:watch");
// every check passes on a bearish lean: fired, BUY PE
m = xrModel(st(Object.assign({}, B, {bias: "BEARISH"}), {ticket: null, wait: null}));
assert.strictEqual(m.fired, true); assert.strictEqual(m.word, "BUY PE"); assert.strictEqual(m.side, -1);
assert.ok(say(m)[0].endsWith("Signal: BUY PE."), say(m)[0]); assert.strictEqual(m.held, false); assert.strictEqual(m.sub, "");
console.log("ok:ready");
// the Signal card's call while the session holds it - Bank Nifty after its 11:03 stop on 27 Oct 2026, the market closed:
// the call is drawn (held, what holds it) rather than NO TRADE, and today's closed ticket is named
m = xrModel(st(Object.assign({}, B, {bias: "BULLISH"}),
               {ticket: {open: false, status: "CLOSED \u2014 stop-loss hit", strike: 54600, option_type: "CE", pnl: -4362},
                wait: {code: "closed", badge: "MARKET CLOSED", base: "The session is over"}}));
assert.strictEqual(m.word, "BUY CE"); assert.strictEqual(m.fired, false); assert.strictEqual(m.held, true);
assert.strictEqual(m.side, 1); assert.strictEqual(m.sub, "held \u00b7 market closed");
assert.strictEqual(m.done, "54600 CE \u00b7 stop-loss hit");
t = say(m)[0];
assert.ok(t.includes("Signal: BUY CE, held - market closed - The session is over.") && t.endsWith("Today's ticket: 54600 CE \u00b7 stop-loss hit."), t);
// held by a check, in session
m = xrModel(st(Object.assign({}, B, {bias: "BEARISH", adx: 17.2, adx_blocked: true}), {ticket: null, wait: {code: "neutral"}}));
assert.strictEqual(m.word, "BUY PE"); assert.strictEqual(m.held, true); assert.strictEqual(m.sub, "held \u00b7 waiting for ADX");
assert.ok(say(m)[0].includes("Signal: BUY PE, held - Waiting for trend strength (ADX)"), say(m)[0]);
// no lean: no call, nothing held
m = xrModel(st(B, {ticket: null, wait: {code: "neutral"}}));
assert.strictEqual(m.word, "NO TRADE"); assert.strictEqual(m.held, false); assert.strictEqual(m.done, "");
console.log("ok:held");
// pass / not on the drawing: a check met, or a vote with the lean, passes (green tick) - on a PE call a down vote passes,
// an up vote does not; no reading is grey; before any lean a vote is shown its own way (null)
assert.strictEqual(xrPass({v: 2}, 0), 1); assert.strictEqual(xrPass({v: -2}, 1), -1); assert.strictEqual(xrPass({v: 0}, 1), 0);
assert.strictEqual(xrPass({v: -1}, -1), 1, "a down vote on a PE call passes");
assert.strictEqual(xrPass({v: 1}, -1), -1, "an up vote on a PE call does not");
assert.strictEqual(xrPass({v: 1}, 1), 1); assert.strictEqual(xrPass({v: 1}, 0), null, "no lean: its own way");
console.log("ok:pass");
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
assert.strictEqual(m.stocks.length, 0); assert.ok(say(m)[0].startsWith("No trade - "), say(m)[0]);
assert.strictEqual(xrModel({indices: {}}), null, "no reading for the index: nothing");
console.log("ok:switch");
// a row for every stock: as tall as the list across a wide box; on a phone, the taller of its two columns
MAPDATA = {index: "SENSEX", tiles: ''' + json.dumps(tiles) + r'''};
m = xrModel(st(B, {ticket: null, wait: {code: "neutral"}}));
assert.strictEqual(m.listed, 6);
assert.strictEqual(xrHeight(900, m), 460, "a short list keeps the least height");
const many = {stocks: Array.from({length: 49}, (_, i) => ({n: "S" + i, p: i < 30 ? 1 : -1}))};
assert.strictEqual(xrHeight(900, many), 18 * 49 + 110, "49 stocks across: a row of 18 each");
assert.strictEqual(xrHeight(375, many), XR_T.top + 29 * XR_T.row + XR_T.gat + XR_T.sig + XR_T.end, "down a phone: the 30-row column");
const [L, R] = xrCols([{n: "a", p: 2}, {n: "b", p: .5}, {n: "c", p: -.1}, {n: "d", p: -3}, {n: "e", p: 0}, {n: "f", p: 0}]);
assert.deepStrictEqual(L.map(x => x.n), ["a", "b", "e"]); assert.deepStrictEqual(R.map(x => x.n), ["d", "c", "f"],
  "rising left, falling right, biggest move first, an unmoved stock under the shorter column");
const box = {clientWidth: 900, clientHeight: 0, style: {height: ""}};
xrFit({box}, many); assert.strictEqual(box.style.height, (18 * 49 + 110) + "px");
console.log("ok:height");
// the map came for this index, but no prices yet: listed, none drawn
MAPDATA = {index: "SENSEX", tiles: [{sym: "A", pct: null}, {sym: "B", pct: null}]};
m = xrModel(st(B, {ticket: null, wait: {code: "neutral"}}));
assert.strictEqual(m.listed, 2); assert.strictEqual(m.stocks.length, 0);
MAPDATA = {index: "SENSEX", tiles: []};
assert.strictEqual(xrModel(st(B, {ticket: null, wait: {code: "neutral"}})).listed, 0, "an index with no list of its own");
console.log("ok:listed");
'''
    rr = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = rr.stdout + rr.stderr
    for tag, name in (("watch", "watching: stocks sorted by move (no-price left out), votes then ADX/Room/R:R, NO TRADE and why"),
                      ("ready", "every check passes on a bearish lean: fired, BUY PE"),
                      ("held", "the Signal card's call while held (market closed / a check): BUY CE, held and why - "
                               "not NO TRADE - and today's closed ticket named"),
                      ("pass", "a check met or a vote with the lean passes (a down vote on a PE call too), against it does not, "
                               "no reading is grey, no lean shows each vote's own way"),
                      ("trade", "an open ticket: IN TRADE with its strike, side from the option"),
                      ("rule", "a rule index: its own conditions are the checks"),
                      ("switch", "another index's map on hand: no stocks rather than the wrong ones"),
                      ("height", "a row for every stock: the box as tall as the list (wide) or its taller column (phone); "
                                 "rising left, falling right"),
                      ("listed", "the list come but no prices yet, and an index with no list: told apart")):
        check(name, f"ok:{tag}" in out, out[-700:] if f"ok:{tag}" not in out else "")

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
