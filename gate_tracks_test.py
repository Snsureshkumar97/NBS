#!/usr/bin/env python3
"""The Dashboard's gate tracks and today's movers (the user, 16 Oct 2026, after AITOS's "gate tracks" and "flow up / flow
down top 20" screens: "yes"). Display only - each index's checks before a ticket as a track, the first one not met is the
HOLD; the index's own stocks ranked by today's move. The page's own functions run in node on readings shaped like
/api/state's; the reading carries the ADX it had to reach. No network."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. THE READING CARRIES THE ADX IT HAD TO REACH")
import feeds
pub = feeds._public({"index": "NIFTY", "spot": 22700.0, "bias": "NEUTRAL", "adx_needed": 20,
                     "technical": {"adx": 17.2, "adx_ok": False}}, "NIFTY")
check("/api/state's reading has adx_needed beside adx", pub.get("adx_needed") == 20 and pub.get("adx") == 17.2,
      (pub.get("adx"), pub.get("adx_needed")))

print("2. THE PAGE (node)")
src = open(os.path.join(HERE, "web_server.py")).read()
pane = src[src.index('<section class="pane" data-pane="gates">'):src.index("</section>", src.index('data-pane="gates"'))]
home = src[src.index('<section class="pane on" data-pane="home">'):src.index("</section>", src.index('data-pane="home"'))]
check("a tab of its own: the button (in the Market menu), the page with both, its name, and it draws them",
      'data-tab="gates"' in src and 'id="kdgates"' in pane and 'id="kdmov"' in pane and '"gates"' in src[src.index("const TABS"):]
      and 'gates:"Gate tracks"' in src and 'if(TAB === "gates") gatesDraw(s);' in src and 'if(name === "gates") gatesDraw(LAST, true);' in src
      and "function gatesDraw(s, force){\n  gateTracks(s);\n  moversDraw(!!force);" in src)
check("the Dashboard is back as it was - neither is drawn there", 'id="kdgates"' not in home and 'id="kdmov"' not in home
      and "gateTracks(s)" not in src[src.index("function homeDraw(s){"):src.index("\n}\n", src.index("function homeDraw(s){"))])
check("the movers only fetch while their page is on screen", 'TAB !== "gates" || document.hidden' in src)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
else:
    a = src.index("const GT_SESSION =")
    z = src.index("let GT_HTML")
    m0 = src.index("function moversRank(")
    m1 = src.index("const MV_INDICES")
    base = dict(spot=22700.0, bias="NEUTRAL", agree=2, dissent=1, adx=24.0, adx_needed=20, adx_blocked=False,
                not_worth_it=False, macd_blocked=False, reach_points=140.0, ticket_gate_ratio=1.4, ticket_gate_need=1.0)
    cases = {
        "neutral": [base, {"ticket": None, "wait": {"code": "neutral", "badge": "NO SIGNAL", "why": "x", "base": "x"}}],
        "adx": [dict(base, adx=17.2, adx_blocked=True), {"ticket": None, "wait": {"code": "neutral", "badge": "NO SIGNAL"}}],
        "room": [dict(base, not_worth_it=True), {"ticket": None, "wait": {"code": "neutral", "badge": "NO SIGNAL"}}],
        "cool": [dict(base, bias="BULLISH"), {"ticket": None, "wait": {"code": "reentry_cooldown", "badge": "COOLDOWN",
                                                                      "why": "w", "base": "20 minutes after the last one"}}],
        "closed": [base, {"ticket": None, "wait": {"code": "closed", "badge": "MARKET CLOSED", "base": "opens 09:15"}}],
        "trade": [dict(base, bias="BULLISH"), {"ticket": {"open": True, "strike": 23150, "option_type": "CE", "pnl": 1250},
                                             "wait": {"code": "position_open", "badge": "POSITION OPEN"}}],
        "ready": [dict(base, bias="BEARISH"), {"ticket": None, "wait": None}],
        "tr": [dict(base, rule={"label": "Trend Rider", "system": "trend_rider", "filters": [
            {"name": "Above EMA 50", "ok": True, "reading": "+42"}, {"name": "ADX rising", "ok": False, "reading": "18.1"},
            {"name": "+DI over -DI", "ok": True, "reading": "26/14"}]}), {"ticket": None, "wait": {"code": "neutral"}}],
    }
    prog = r'''
const assert = require("assert");
const esc = s => String(s == null ? "" : s).replace(/[&<>]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]));
const num = (v, d = 2) => v == null || isNaN(v) ? "—" : Number(v).toFixed(d);
const money = v => (v >= 0 ? "+" : "-") + "Rs" + Math.abs(Math.round(v));
''' + src[a:z] + src[m0:m1] + r'''
const C = ''' + json.dumps(cases) + r''';
const run = k => { const [r, ts] = C[k]; const g = gateSteps(r, ts); return [g, gateStage(r, ts, g), gateRow(k, r, ts)]; };
let [g, st, h] = run("neutral");
assert.deepStrictEqual(g.steps.map(x => x.n), ["Data", "Votes", "ADX", "Room", "R:R", "Ticket", "Market"]);
assert.strictEqual(st[0], "Watching"); assert.strictEqual(st[3], 1, "the HOLD is the votes");
assert.ok(h.includes('class="gt-hold"') && st[2].includes("2 for") && st[2].includes("1 against"), "the votes' count where it waits");
assert.ok(h.includes('class="gt-met"'), "ADX already met though not reached yet: drawn as met, not as a pass");
console.log("ok:neutral");
[g, st, h] = run("adx");
assert.strictEqual(st[0], "Building"); assert.strictEqual(st[3], 2);
assert.ok(st[2].includes("trend strength (ADX)") && st[2].includes("ADX 17.2") && st[2].includes("needs 20"), st[2]);
assert.ok(run("adx")[2].includes('title="ADX \u00b7 ADX 17.2'), "the dot's own title carries its reading");
console.log("ok:adx");
[g, st] = run("room");
assert.strictEqual(st[3], 3, "the HOLD is the room"); assert.ok(st[2].includes("Waiting for room to run"), st[2]);
console.log("ok:room");
[g, st] = run("cool");
assert.strictEqual(st[0], "COOLDOWN"); assert.strictEqual(g.steps[st[3]].n, "Ticket");
assert.ok(st[2].includes("20 minutes"), st[2]);
console.log("ok:cool");
[g, st, h] = run("closed");
assert.strictEqual(st[0], "MARKET CLOSED"); assert.strictEqual(g.steps[st[3]].n, "Market");
assert.ok(!h.includes('class="gt-done"'), "a closed market draws nothing as passed");
console.log("ok:closed");
[g, st, h] = run("trade");
assert.strictEqual(st[0], "In trade"); assert.ok(st[2].includes("23150 CE") && st[2].includes("+Rs1250"), st[2]);
assert.ok(!h.includes("gt-hold") && (h.match(/class="gt-done"/g) || []).length === 7, "in a trade every step reads done");
console.log("ok:trade");
[g, st] = run("ready");
assert.strictEqual(st[0], "Entry ready"); assert.strictEqual(st[3], -1);
console.log("ok:ready");
[g, st, h] = run("tr");
assert.deepStrictEqual(g.steps.map(x => x.n), ["Data", "Above EMA 50", "ADX rising", "+DI over -DI", "Ticket", "Market"]);
assert.strictEqual(g.steps[st[3]].n, "ADX rising"); assert.ok(h.includes("Trend Rider"), "the system is named");
const tv = {spot: 1, rule: {system: "tv_rating", label: "TradingView rating", filters: [{name: "TradingView overall rating", ok: false}]}};
const gtv = gateSteps(tv, {wait: null});
assert.strictEqual(gtv.steps[1].n, "TV overall rating", "TradingView shortened to TV on the dot");
assert.ok(gateRow("BANKNIFTY", tv, {wait: null}).includes("<small>TradingView</small>"), "and named TradingView");
console.log("ok:tr");
const m = moversRank([{sym: "A", pct: 1.2}, {sym: "B", pct: -2.5}, {sym: "C", pct: 0.4}, {sym: "D", pct: null},
                      {sym: "E", pct: 3.1}, {sym: "F", pct: -0.2}, {sym: "G", pct: 0}], 2);
assert.deepStrictEqual(m.up.map(x => x.sym), ["E", "A"]); assert.deepStrictEqual(m.down.map(x => x.sym), ["B", "F"]);
assert.strictEqual(m.max, 3.1); assert.strictEqual(m.total, 6, "a stock with no price is left out");
console.log("ok:movers");
'''
    rr = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = rr.stdout + rr.stderr
    for tag, name in (("neutral", "votes not agreeing: WATCHING, the HOLD on the votes with their count; a later met "
                                  "check drawn as met"),
                      ("adx", "votes agree, ADX too low: BUILDING, 'Waiting for trend strength (ADX) - ADX 17.2 - needs 20'"),
                      ("room", "not enough room: the HOLD on room to run"),
                      ("cool", "a cooldown: the ticket's own badge and reason, the HOLD on 'Ticket'"),
                      ("closed", "market closed: the session is the HOLD, nothing drawn as passed"),
                      ("trade", "in a trade: IN TRADE with the strike and P&L, every step done"),
                      ("ready", "every check passes: ENTRY READY"),
                      ("tr", "a Trend Rider index: its own conditions are the track, named"),
                      ("movers", "movers: top rising and falling by today's move, no-price stocks left out")):
        check(name, f"ok:{tag}" in out, out[-500:] if f"ok:{tag}" not in out else "")

print("3. TODAY'S MOVERS ON THE SIGNAL PAGE, WHERE THE AI DESK'S CHECKS WERE (\"add those top movers over there per index\")")
sig = src[src.index('<section class="pane" data-pane="signal">'):src.index("</section>", src.index('data-pane="signal">'))]
check("its box sits right after room to run on the Signal card, and the heat map's own fetch draws it (no fetch of its own)",
      '<div class="room" id="room"></div>\n  <div class="room" id="sigmovers"></div>' in sig
      and "moverDraw(); pulseDraw(); sigMovers();" in src and "#sigmovers{order:18;grid-column:2}" in src)
if NODE:
    a1 = src.index("function moversRank(")
    a2 = src.index("const MV_INDICES")
    prog2 = r'''
const assert = require("assert");
const esc = s => String(s == null ? "" : s).replace(/[&<>]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]));
const num = (v, d = 2) => Number(v).toFixed(d);
const EL = {sigmovers: {innerHTML: ""}}; const $ = id => EL[id];
let MAPDATA = null, CUR = "NIFTY";
''' + src[a1:a2] + r'''
const tiles = [["A", 1.2], ["B", -2.5], ["C", 0.4], ["D", 3.1], ["E", -0.2], ["F", 2.2], ["G", 0.9], ["H", -1.1], ["I", -0.7],
               ["J", 0.1], ["K", -3.0], ["L", 0.05]].map(([s, p]) => ({sym: s, pct: p, sector: "x"}));
MAPDATA = {index: "NIFTY", tiles, breadth: {up: 7, down: 5, flat: 0, total: 12}};
sigMovers();
let h = EL.sigmovers.innerHTML;
assert.ok(h.includes("Today's movers &middot; NIFTY's stocks"), "titled for the index on screen");
assert.strictEqual((h.match(/class="mv-r"/g) || []).length, 10, "5 rising and 5 falling");
assert.ok(h.indexOf(">D<") < h.indexOf(">F<") && h.indexOf(">K<") < h.indexOf(">B<"), "biggest first on each side");
assert.ok(h.includes("7 up &middot; 5 down of 12") && h.includes("not part of the signal"), "breadth line, marked context");
MAPDATA = {index: "BANKNIFTY", tiles}; sigMovers();
assert.strictEqual(EL.sigmovers.innerHTML, "", "another index's data is never shown under this one");
MAPDATA = {index: "NIFTY", tiles: []}; sigMovers();
assert.strictEqual(EL.sigmovers.innerHTML, "", "no stock list (Midcap) or Exness: nothing");
console.log("ok:sig");
'''
    r2 = subprocess.run([NODE, "-e", prog2], capture_output=True, text=True, timeout=60)
    check("the index on screen's 5 rising and 5 falling, biggest first, never another index's, nothing without a stock list",
          "ok:sig" in r2.stdout, (r2.stdout + r2.stderr)[-500:])

print()
print("GATE TRACKS TEST PASSED" if not fails else f"GATE TRACKS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
