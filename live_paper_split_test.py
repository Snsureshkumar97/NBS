#!/usr/bin/env python3
"""Live trades and paper trades as two figures, never one (the user, 7 Oct 2026: "can you make in the dashboard live trades
and paper trades separate it shows in same"). The Dashboard's headline used to be "Today's result" - paper tickets and real
fills added together - with the real money as a small line under it. Now: the log's split in one read, the feed's two
figures, and the page's own functions (run in node): two headline figures, every open trade tagged Live or Paper, the side
column's Today box, Home's strip and the Record recap the same. Fakes only: temp logs, temp fills, a fake executor."""
import csv, json, os, shutil, subprocess, sys, tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import feeds, trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

TODAY = feeds.now_ist().strftime("%Y-%m-%d")
def row(tid, event, entry, exit=None, pnl=None, date=TODAY):
    r = {k: "" for k in trade_log.FIELDS}
    r.update(trade_id=tid, event=event, date=date, time_ist="10:00:00", index="NIFTY", strike="22600", option_type="PE",
             entry=str(entry), lot_size="65", lots="1", status="OPEN")
    if event == "CLOSE":
        r.update(exit="" if exit is None else str(exit), pnl="" if pnl is None else str(pnl), status="CLOSED - stop hit")
    return r
def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trade_log.FIELDS); w.writeheader(); w.writerows(rows)
def fills(path, rows):
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
def fill(tid, entry, exit, qty=65):
    return {"trade_id": tid, "index": "NIFTY", "source": "rule", "qty": qty, "entry_avg": entry, "paper_entry": None,
            "exit_avg": exit, "exit_qty_priced": qty, "sold_outside_qty": 0, "gross_pnl": round((exit - entry) * qty, 2)}

print("1. THE LOG, SPLIT IN ONE READ")
LOG = os.path.join(tempfile.mkdtemp(), "trades.csv")
write(LOG, [row("L1", "OPEN", 151.7), row("L1", "CLOSE", 151.7, 107.8, -2853.5),       # live: real fill 150.6 -> 108.95
            row("P1", "OPEN", 694.0), row("P1", "CLOSE", 694.0, 571.0, -3690.0),        # paper (Bank Nifty's switch off)
            row("P2", "OPEN", 328.25), row("P2", "CLOSE", 328.25, 197.25, -8515.0),     # paper (short of funds)
            row("S1", "OPEN", 100.0), row("S1", "CLOSE", 100.0),                        # "the tool stopped": no price
            row("Y1", "OPEN", 90.0, date="2020-01-01"), row("Y1", "CLOSE", 90.0, 95.0, 325.0, date="2020-01-01")])
fills(LOG + ".live.fills.jsonl", [fill("L1", 150.6, 108.95)])
sp = trade_log.booked_split_today(TODAY, LOG)
check("live: the real fill's result, (108.95 - 150.6) x 65", sp["live"] == (round((108.95 - 150.6) * 65, 2), 1), sp["live"])
check("paper: the two trades that placed no order, at the tool's prices", sp["paper"] == (-12205.0, 2), sp["paper"])
check("a close with no price counts in neither; another day's in neither", sp["live"][1] + sp["paper"][1] == 3)
check("the live trade's id, so a list of closed trades can be tagged", sp["live_ids"] == {"L1"}, sp["live_ids"])
check("live_booked_today is the same live figure (one source)", trade_log.live_booked_today(TODAY, LOG) == sp["live"])

print("2. THE FEED'S TWO FIGURES")
class Ex:
    def __init__(self, fills): self.fills = fills
    def real_entry(self, tid): return self.fills.get(tid)
f = feeds.Feed("t:lps", "lps@example.invalid", "nse_index")
write(f.tickets.path, [row("L1", "OPEN", 151.7), row("L1", "CLOSE", 151.7, 107.8, -2853.5),
                       row("P1", "OPEN", 694.0), row("P1", "CLOSE", 694.0, 571.0, -3690.0)])
fills(f.tickets.path + ".live.fills.jsonl", [fill("L1", 150.6, 108.95)])
def tpub(tid, pnl, real):
    t = {"open": True, "trade_id": tid, "tracked_on": "premium", "entry": 130.0, "now": 120.0, "lots": 1, "lot_size": 65, "pnl": pnl}
    if real: t.update(entry_real=True, entry_venue="Zerodha")
    return {"ticket": t}
ai_live = {"open": True, "trade_id": "AI-L", "tracked_on": "premium", "entry": 90.0, "now": 100.0, "lots": 1, "lot_size": 65, "pnl": 650.0}
ai_paper = {"open": True, "trade_id": "AI-P", "tracked_on": "premium", "entry": 50.0, "now": 40.0, "lots": 1, "lot_size": 65, "pnl": -650.0}
f.live = Ex({"AI-L": (92.0, "Zerodha")})
f.ai = type("A", (), {"book": type("B", (), {"path": os.path.join(os.path.dirname(f.tickets.path), "ai_trades.csv"),
                                              "public": staticmethod(lambda name: {"ticket": dict(ai_live if name == "NIFTY" else ai_paper)}
                                                                     if name in ("NIFTY", "SENSEX") else {"ticket": None})})()})()
live, paper, _today = f._pnl_split({"NIFTY": tpub("R-L", -1414.0, True), "SENSEX": tpub("R-P", -3017.0, False), "BANKNIFTY": {"ticket": None}})
check("live: booked from the fill + the rule ticket WITH a real position + the AI desk's real one (from its fill, 8 x 65)",
      live["booked"] == round((108.95 - 150.6) * 65, 2) and live["open"] == -1414.0 and live["ai_open"] == 520.0
      and live["net"] == round(live["booked"] - 1414.0 + 520.0, 2), live)
check("paper: booked from the paper close + the rule ticket with NO real position + the AI desk's paper one",
      paper["booked"] == -3690.0 and paper["closed"] == 1 and paper["open"] == -3017.0 and paper["open_n"] == 1
      and paper["ai_open"] == -650.0 and paper["net"] == round(-3690.0 - 3017.0 - 650.0, 2), paper)
check("no trade is in both", live["open_n"] + paper["open_n"] == 2 and live["ai_open_n"] + paper["ai_open_n"] == 2)
cl = {c["trade_id"]: c for c in _today["closed"]}
check("the Positions table's closed rows: each with its qty (lots x lot size) and the LIVE one at its real fill prices",
      cl["L1"]["qty"] == 65 and cl["L1"]["entry"] == 150.6 and cl["L1"]["exit"] == 108.95 and cl["L1"]["live"]
      and cl["L1"]["strike"] == 22600 and cl["P1"]["live"] is None and cl["P1"]["entry"] == 694.0 and cl["L1"]["source"] == "rule",
      _today["closed"])
check("...newest first, with its close time", [c["trade_id"] for c in _today["closed"]] == sorted(cl, key=lambda i: cl[i]["closed"], reverse=True))
ai = {r["entry_real"]: r for r in _today["ai_open"]}
check("the AI desk's open trades, each saying whether it is a real order", set(ai) == {True, False} and ai[True]["entry"] == 92.0, _today["ai_open"])
check("the paper figure names today's live trade ids", paper["live_ids"] == ["L1"], paper["live_ids"])
check("_live_pnl is still the live figure (its callers unchanged)", f._live_pnl({"NIFTY": tpub("R-L", -1414.0, True)})["open"] == -1414.0)
g = feeds.Feed("t:lps2", "lps2@example.invalid", "nse_index")
g.live, g.ai = None, None
gl, gp, _ = g._pnl_split({"NIFTY": tpub("R-P", 500.0, False)})
check("an account with no live orders: no live figure, and every trade is paper", gl is None and gp["open"] == 500.0, (gl, gp))
f.tickets.closed[:] = [{"trade_id": "L1", "index": "NIFTY", "strike": 22600, "option_type": "PE", "pnl": -2715.0, "exit_time": "10:29:40"},
                       {"trade_id": "P1", "index": "BANKNIFTY", "strike": 54600, "option_type": "PE", "pnl": -3690.0, "exit_time": "10:06:10"}]
snap = f.snapshot()
rec = {r["trade_id"]: r.get("live") for r in snap["session"]["recent"]}
check("the snapshot carries both figures", snap.get("live_pnl") and snap.get("paper_pnl") is not None, list(snap)[-3:])
check("each recently closed ticket says whether it was live", rec == {"L1": True, "P1": False}, rec)
check("...without changing the book's own rows", "live" not in f.tickets.closed[0])

print("3. THE PAGE")
SRC0 = open(os.path.join(HERE, "web_server.py")).read()
check("the state payload hands the page both figures (the browser check caught it missing)",
      '"live_pnl": snap.get("live_pnl")' in SRC0 and '"paper_pnl": snap.get("paper_pnl")' in SRC0)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
SRC = open(os.path.join(HERE, "web_server.py")).read()
def grab(start, end="\n}\n"):
    a = SRC.index(start)
    return SRC[a:SRC.index(end, a) + len(end)]
if not NODE:
    check("node is available to run the page's own functions", False, "install node to run this section")
else:
    n0 = SRC.index("const num=(v,d=2)")
    n1 = SRC.index("\n", SRC.index("const esc=s=>", n0)) + 1
    helpers = SRC[SRC.index("function livePnl(s){"):SRC.index("function kiteSide(s){")]
    prog = ("const assert = require('assert');\n" + SRC[n0:n1] + 'let CCY = "INR";\n' + grab("function ccySym(){", "\n")
            + grab("function ccyLocale(){", "\n") + grab("function money(v, signed){") + grab("function fundsLabel(f){")
            + "let KSIDE_HTML = '', KDASH_HTML = '', CUR = 'NIFTY', DESK = [], MKT_ROWS = null;\nconst lotsDash = s => {};   // per_index_lots_test.py\n" + helpers
            + grab("function kiteSide(s){") + grab("function posTable(s){") + grab("function posCount(s){") + "let KPOS_HTML = '';\n"
            + grab("function posPane(s){") + grab("function kiteDash(s){") + grab("function homeDraw(s){") + grab("function recapDraw(s){")
            + r'''
const els = {};
const $ = id => els[id] || (els[id] = {id, innerHTML: "", textContent: "", dataset: {}, style: {}, className: "",
  getAttribute(){ return null; }, addEventListener(){} });
function switchMarketShortcut(){} function selectIndex(){} function showTab(){}
const tk = (o) => ({ticket: Object.assign({open: true, status: "OPEN", strike: 22550, option_type: "PE", entry: 133.8, now: 126.55,
  stop: 91.05, targets: [151.4, 164.4, 177.4], hit: {}}, o)});
const s = {
  session: {issued: 5, wins: 0, stops: 3, booked: -9999, open: -9999, net: -19998,      // the old mixed figure - must not show
            recent: [{trade_id: "L1", index: "NIFTY", strike: 22600, option_type: "PE", exit_time: "10:29:40", pnl: -8122, live: true},
                     {trade_id: "P1", index: "BANKNIFTY", strike: 54600, option_type: "PE", exit_time: "10:06:10", pnl: -11070, live: false}]},
  tickets: {NIFTY: tk({entry_real: true, pnl: -1414}), SENSEX: tk({strike: 72600, pnl: -3017}), BANKNIFTY: {ticket: null}},
  live_pnl: {booked: -8122, closed: 1, open: -1414, open_n: 1, ai_open: 0, ai_open_n: 0, venue: "Zerodha", net: -9536},
  paper_pnl: {booked: -18930, closed: 2, open: -3017, open_n: 1, ai_open: 0, ai_open_n: 0, net: -21947, live_ids: ["L1"]},
  order: ["NIFTY", "BANKNIFTY", "SENSEX"],
  indices: {NIFTY: {spot: 22592, bias: "BEARISH"}, BANKNIFTY: {spot: 54949, bias: "NEUTRAL"}, SENSEX: {spot: 72618, bias: "BEARISH"}},
  broker: {}, market_open: true,
};
const lv = livePnl(s), pp = paperPnl(s);
assert.strictEqual(lv.net, -8122 - 1414, "live: booked + the live ticket's open, from the tickets");
assert.strictEqual(pp.net, -18930 - 3017, "paper: booked + the paper ticket's open, from the tickets - not the live one");
assert.strictEqual(pp.open_n, 1);
kiteDash(s);
const d = els.kdash.innerHTML;
assert.ok(d.includes("Live trades &middot; real money") && d.includes("Paper trades"), "two headline figures");
assert.ok(!d.includes("Today's result"), "the mixed headline is gone");
assert.ok(d.includes(money(-9536)) && d.includes(money(-21947)), "each figure is its own total");
assert.ok(!d.includes(money(-19998)), "the old mixed net is not shown");
const nifty = d.split('data-k="NIFTY"')[1].split("</tr>")[0], sensex = d.split('data-k="SENSEX"')[1].split("</tr>")[0];
assert.ok(nifty.includes('jbadge live') && nifty.includes(">Live<"), "the Nifty trade (a real order) is tagged Live");
assert.ok(sensex.includes(">Paper<") && !sensex.includes("jbadge live"), "the Sensex trade (no order) is tagged Paper");
kiteSide(s);
const k = els.kside.innerHTML;
assert.ok(k.includes("<td>Live</td>") && k.includes("<td>Paper</td>") && k.includes("jbadge live"), "the side column too - its Today table (7 Oct 2026)");
assert.ok(!k.includes("&amp;middot;"), "no escaped entity shown as text (the side column's labels are escaped)");
assert.ok(!k.includes(money(-19998)), "...without the mixed figure");
homeDraw(s);
assert.ok(els.htoday.innerHTML.includes("Live trades") && els.htoday.innerHTML.includes("Paper trades") && !els.htoday.innerHTML.includes(">Net<"), "Home's strip");
recapDraw(s);
assert.ok(els.recap.innerHTML.includes("Live trades") && els.recap.innerHTML.includes("Paper trades") && !els.recap.innerHTML.includes(">Net<"), "the Record recap");
const lines = els.recaplist.innerHTML.split("<tbody>")[1].split("</tr>");
assert.ok(lines[0].includes(">Live<") && lines[1].includes(">Paper<") && els.recaplist.innerHTML.includes(">P&amp;L<"),
          "each closed trade in the recap is tagged - a row of its table (7 Oct 2026)");
// THE POSITIONS TABLE (the user, 7 Oct 2026, with Kite's Positions page: "make the dash board with qty enter ltp and now.
// ltp as well add it" ... "with the strike value as well")
const ps = Object.assign({}, s, {
  tickets: {NIFTY: tk({entry_real: true, pnl: -1414, lots: 3, lot_size: 65}), SENSEX: tk({strike: 72600, entry: 315.4, now: 291.3, pnl: -4338, lots: 3, lot_size: 20}), BANKNIFTY: {ticket: null}},
  positions_today: {ai_open: [], closed: [
    {index: "NIFTY", strike: 22600, option_type: "PE", lots: 3, lot_size: 65, qty: 195, entry: 150.6, exit: 108.95, pnl: -8121.75, live: "zerodha", closed: "10:29:40", source: "rule"},
    {index: "BANKNIFTY", strike: 54600, option_type: "PE", lots: 3, lot_size: 30, qty: 90, entry: 694, exit: 571, pnl: -11070, live: null, closed: "10:06:10", source: "ai"}]}});
// ...on a page of its own since 7 Oct 2026 ("and postions can you give a separate tab"); the Dashboard links to it
kiteDash(ps);
assert.ok(!els.kdash.innerHTML.includes('class="kd-tab kd-pos"') && els.kdash.innerHTML.includes('data-kact="positions"')
          && els.kdash.innerHTML.includes(">2 open<") && els.kdash.innerHTML.includes("2 closed today"),
          "the Dashboard links to Positions instead of holding it");
posPane(ps);
const sm = els.kpossum.innerHTML;
assert.ok(sm.includes("<span>Open now</span>") && sm.includes(money(-1414 - 4338)) && sm.includes("2 positions")
          && sm.includes("<span>Booked today</span>") && sm.includes(money(-8121.75 - 11070)) && sm.includes("2 closed")
          && sm.includes("Live \u00b7 real money") && sm.includes("<span>Paper</span>"), "the page's headline figures: " + sm);
assert.strictEqual(els.kposn.textContent, "2 open \u00b7 2 closed");
const pt = els.kpos.innerHTML.split('class="kd-tab kd-pos"')[1] || "";
const prow = pt.split("<tbody>")[1].split("</tbody>")[0].split("</tr>").filter(r => r.includes("<td"));
assert.strictEqual(prow.length, 4, "two open trades and two closed ones");
assert.ok(prow[0].includes(">NIFTY<") && prow[0].includes("22550 PE") && prow[0].includes(">195<") && prow[0].includes("133.80")
          && prow[0].includes("126.55") && prow[0].includes(">Live<") && prow[0].includes(">open<"),
          "an open live trade: its own strike column, qty (3 lots x 65), entry, LTP now, Live - " + prow[0]);
assert.ok(prow[1].includes("72600 PE") && prow[1].includes(">60<") && prow[1].includes(">Paper<"), "an open paper trade: qty 3 x 20, Paper");
assert.ok(prow[2].includes("22600 PE") && prow[2].includes("150.60") && prow[2].includes("108.95") && prow[2].includes(">exit<")
          && prow[2].includes("closed 10:29") && prow[2].includes(">Live<") && prow[2].includes(money(-8121.75)), "a closed live trade: the fill prices, its exit");
assert.ok(prow[3].includes(">AI<") && prow[3].includes(">Paper<") && prow[3].includes(">90<"), "the AI desk's trade is badged AI");
const foot = pt.split("<tfoot>")[1];
assert.ok(foot.includes(money(-1414 - 8121.75)) && foot.includes(money(-4338 - 11070)), "the table's totals: live and paper apart");
assert.ok(pt.includes("<th>Strike</th>") && pt.includes(">Qty<") && pt.includes(">Entry<") && pt.includes("LTP / exit"), "the columns");
posPane(Object.assign({}, s, {tickets: {}, positions_today: {closed: [], ai_open: []}}));
assert.ok(els.kpos.innerHTML.includes("No trade yet today."), "an empty day says so");
const gold = Object.assign({}, s, {indices: {GOLD: {spot: 4130, bias: "BEARISH", cfd: true}}, order: ["GOLD"],
  tickets: {GOLD: tk({strike: 4140, option_type: "PE", lots: 0.1, lot_size: 100, cfd: true, entry: 4136.6, now: 4130.2, pnl: 64})}, positions_today: {closed: [], ai_open: []}});
posPane(gold);
const g = els.kpos.innerHTML.split('class="kd-tab kd-pos"')[1];
assert.ok(g.includes("0.10 lot") && g.includes(">Sell<") && !g.includes("4140 PE"), "a CFD: lots, and its side where a strike would be");
// an open Exness trade is named by its side on the Dashboard and in the side column, never "83200 PE" (7 Oct 2026)
const btc = Object.assign({}, s, {indices: {BTC: {spot: 83080.92, bias: "BEARISH", cfd: true}}, order: ["BTC"],
  tickets: {BTC: tk({strike: 83200, option_type: "PE", cfd: true, lots: 0.1, lot_size: 1, entry: 83083.72, now: 83080.92, pnl: -0.28, entry_real: true})},
  positions_today: {closed: [], ai_open: []}});
CUR = "BTC"; kiteDash(btc); kiteSide(btc);
const brow = els.kdash.innerHTML.split('data-k="BTC"')[1].split("</tr>")[0];
assert.ok(brow.includes("<td>Sell") && !brow.includes("83200"), "the Dashboard's open trade: its side - " + brow);
assert.ok(els.kside.innerHTML.includes("BTC \u00b7 Sell") && !els.kside.innerHTML.includes("83200"), "the side column's heading: its side");
CUR = "NIFTY";
const none = Object.assign({}, s, {live_pnl: null, tickets: {SENSEX: tk({pnl: -3017})}});
kiteDash(none);
assert.ok(els.kdash.innerHTML.includes("No live order today"), "a day with no live order says so, at zero");
console.log("ok:page");
''')
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    check("the page's own functions: two figures everywhere, every trade tagged Live or Paper", "ok:page" in r.stdout and r.returncode == 0, out[-900:])
check("Positions is a tab of its own, right after the Dashboard, and redrawn on every poll while open (7 Oct 2026)",
      '<section class="pane" data-pane="positions">' in SRC and 'const TABS = ["home", "positions",' in SRC
      and SRC.index('data-tab="home" role="tab"') < SRC.index('data-tab="positions" role="tab"') < SRC.index('data-tab="signal" role="tab"')
      and 'if(TAB === "positions") posPane(s);' in SRC and 'if(name === "positions" && LAST) posPane(LAST);' in SRC)
check("on a phone Positions sits in the bottom bar after Home, and More does not light up for it",
      SRC.index('<nav class="botnav"') < SRC.index('data-tab="positions" type="button"') < SRC.index('<button id="bnmore"')
      and '!["home", "positions", "signal", "chart", "chain"].includes(name)' in SRC)
check("the trade settings are a tab of their own, and the bulk buttons sit on Positions, not the Dashboard (7 Oct 2026)",
      SRC.index('<section class="pane" data-pane="tradeset">') < SRC.index('<div class="kdlots" id="kdlots"></div>')
      < SRC.index('<section class="pane" data-pane="journal">')
      and SRC.index('<section class="pane" data-pane="positions">') < SRC.index('<div class="dashbulk" id="dashbulk">')
      < SRC.index('<section class="pane" data-pane="tradeset">')
      and 'if(TAB === "tradeset") lotsDash(s);' in SRC and "  kiteDash(s);\n  lotsDash(s);" not in SRC)

print()
print("LIVE PAPER SPLIT TEST PASSED" if not fails else f"LIVE PAPER SPLIT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
