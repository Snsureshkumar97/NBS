#!/usr/bin/env python3
"""The open ticket on the charts: its own levels, its entry, and its P&L.

17 Sep 2026, asked for by the user: "does it show P&L on the chart from the
entry of our signal". It did not, and the index chart drew the LIVE signal's
levels even while a ticket was open - which can drift away from the frozen ones.

Source checks, plus the ticket payload the page reads. The drawing was checked in
a browser on an isolated copy of the server (its own temporary home, no broker)
with synthetic candles: +Rs1,088 (+12.1%) on a premium ticket, +20 pts (+0.07%)
on one tracked on the index, no badge without a ticket, -Rs1,770 (-19.7%) in the
contract popup.
"""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()

print("1. THE TICKET CARRIES WHAT THE CHART NEEDS")
trade = {"index": "NIFTY", "strike": 23200, "expiry": "2026-09-22", "option_type": "CE",
         "status": "OPEN", "entry_time": "09:31:02", "entry_ltp": 120.0, "entry_spot": 23180.0,
         "use_premium": True, "lot_size": 75, "lots": 1,
         "premium_targets": [150.0, 170.0, 190.0], "premium_sl": 85.0,
         "index_targets": [23230.0, 23260.0, 23290.0], "index_sl": 23135.0,
         "hit": {"T1": False, "T2": False, "T3": False}, "hit_time": {"T1": None, "T2": None, "T3": None},
         "sl_hit": False, "sl_hit_time": None}
pub = tickets.TicketBook()._public_trade(trade, None, 134.5)
check("the frozen index levels", pub["index_targets"] == [23230.0, 23260.0, 23290.0] and pub["index_stop"] == 23135.0)
check("where the index was at entry, even for a premium ticket", pub["entry_spot"] == 23180.0, pub.get("entry_spot"))
check("the live P&L the signal card shows", pub["pnl"] == round((134.5 - 120.0) * 75, 2), pub["pnl"])

print("2. THE INDEX CHART")
# (8 Oct 2026: the chart is TradingView's Lightweight Charts now - the levels are price lines, the badge sits over it.)
check("reads the open ticket from the live state, like the signal card",
      "function openTicketFor(key){" in SRC and "chLevels(d, openTicketFor(CH.key), C)" in SRC
      and "chBadge(openTicketFor(CH.key));" in SRC)
check("draws the ticket's frozen levels while it is open, the live signal's otherwise",
      "stop: TK.index_stop, entry: TK.entry_spot}" in SRC and ": ((d && d.levels) || {});" in SRC)
check("an Entry line among the levels", '[L.entry, "Entry", C.warn, "dash"]' in SRC)
check("the levels are inside the price range the chart scales to",
      "autoscaleInfoProvider: original => chWiden(original(), LWC.levelVals)" in SRC)
check("the P&L badge only while a ticket is open",
      "const p = t ? ticketPnl(t) : null;\n  if(!p){ el.hidden = true; return; }" in SRC)

import shutil, subprocess
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
def fn(name):
    a = SRC.index(f"function {name}(")
    return SRC[a:SRC.index("\n}\n", a) + 3]
if not NODE:
    check("node is available", False, "install node to run the chart's own functions")
else:
    lines = SRC.splitlines()
    pick = lambda start: next(l for l in lines if l.startswith(start))
    prog = "\n".join([pick("const esc=s=>"), pick("const num=(v,d=2)=>"), lines[lines.index(pick("const num=(v,d=2)=>")) + 1],
                      'let CCY = "INR";', fn("ccySym"), fn("ccyLocale"), fn("money"), fn("chLevels"), fn("chWiden"),
                      fn("onColour"), fn("ticketPnl"), fn("chBadge")]) + r"""
const assert = require("assert");
const C = {up: "#0a0", down: "#a00", warn: "#fa0"};
const d = {levels: {t1: 101, t2: 102, t3: 103, stop: 98}, room: {up_to: 110, down_to: 90}};
// no ticket: the live signal's levels, room to run dotted
let lv = chLevels(d, null, C);
assert.deepStrictEqual(lv.map(a => a.label), ["T1", "T2", "T3", "SL", "Room ↑", "Room ↓"]);
assert.deepStrictEqual(lv.filter(a => a.style === "dot").map(a => a.label), ["Room ↑", "Room ↓"]);
// an open ticket: ITS frozen levels and its entry, whatever the live signal says now
const TK = {index_targets: [201, 202, 203], index_stop: 198, entry_spot: 200};
lv = chLevels(d, TK, C);
assert.deepStrictEqual(lv.slice(0, 5).map(a => [a.label, a.v]), [["T1", 201], ["T2", 202], ["T3", 203], ["SL", 198], ["Entry", 200]]);
assert.strictEqual(lv.find(a => a.label === "Entry").colour, C.warn);
// the axis: candles 100-104 take in a level just outside (106) but not a far stale one (120, more than 0.9 of the span away)
const r = chWiden({priceRange: {minValue: 100, maxValue: 104}, margins: {above: 1}}, [106, 120, 97]);
assert.deepStrictEqual(r.priceRange, {minValue: 97, maxValue: 106});
assert.deepStrictEqual(r.margins, {above: 1});
assert.strictEqual(chWiden(null, [1]), null, "no candles in view: nothing to widen");
// the badge: shown with the ticket's P&L, hidden without
const EL = {hidden: true, style: {}, textContent: ""};
const $ = id => EL;
const css = v => ({"--up": "#2fbf71", "--down": "#e5534b"})[v];
const contractName = (i, k, o) => `${i} ${k} ${o}`;
let LAST = {indices: {}};
chBadge({index: "NIFTY", strike: 23200, option_type: "CE", entry: 120, now: 134.5, pnl: 1087.5});
assert.ok(!EL.hidden && EL.textContent === "NIFTY 23200 CE · P&L +₹1,088 (+12.1%)" && EL.style.background === "#2fbf71", JSON.stringify(EL));
chBadge(null);
assert.ok(EL.hidden, "no ticket, no badge");
console.log("ok:levels");
"""
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("in node: the ticket's frozen levels over the live signal's; the axis takes in near levels only; the badge "
          "shows the ticket's P&L and hides without one", "ok:levels" in r.stdout and r.returncode == 0,
          ((r.stdout or "") + (r.stderr or ""))[-900:])

print("3. THE P&L ITSELF")
body = SRC[SRC.index("function ticketPnl(t){"):SRC.index("function chBadge(")]
check("a premium ticket: money and % of the premium paid",
      "money(t.pnl)" in body and "(t.now - t.entry) / t.entry * 100" in body)
check("a ticket tracked on the index: points, signed for a put",
      't.option_type === "PE" ? e - spot : spot - e' in body and "pts" in body)
check("the sign is in the text, not only the colour", '"+" : "\u2212"' in body and body.count('"+" : "\u2212"') == 3)

print("4. THE CONTRACT POPUP")
check("the badge when the popup IS the open ticket's contract - the same P&L the signal card computes",
      "String(tk.strike) === String(OC.strike) && tk.option_type === OC.side" in SRC.split("function ocPnl()")[1][:1200]
      and "ticketPnl(tk)" in SRC.split("function ocPnl()")[1][:1200])
check("it keeps pace with the signal card while open",
      "setInterval(() => { if(OC.open && !document.hidden) ocPnl(); }, 1500);" in SRC)

print("CHART PNL TEST PASSED" if not fails else f"CHART PNL TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
