#!/usr/bin/env python3
"""What the LIVE orders have made, shown beside the other P&L (25 Sep 2026).

The user: "add live order P&L also wherever it needs with the other P&L". The tool's Today total mixes tickets that placed real
orders with tickets that placed none. The real money has its own figure now: today's closed live trades (from the fills) plus
every open ticket that has a real position (worked from its fill) - in the Today box, the Dashboard, Home, and as a filter and
a tag in the Journal. Fakes only: temp logs, temp fills, a fake executor.
"""
import csv, datetime as dt, json, os, shutil, subprocess, sys, tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config, feeds, journal, real_entry, trade_log, web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

TODAY = feeds.now_ist().strftime("%Y-%m-%d")
YEST = (feeds.now_ist() - dt.timedelta(days=1)).strftime("%Y-%m-%d")
def row(tid, event, entry, exit=None, pnl=None, date=TODAY, lots=1, lot_size=65, index="NIFTY", status="OPEN"):
    r = {k: "" for k in trade_log.FIELDS}
    r.update(trade_id=tid, event=event, date=date, time_ist="14:05:35", index=index, strike="23150", option_type="CE",
             entry=str(entry), lot_size=str(lot_size), lots=str(lots), status=status)
    if event == "CLOSE":
        r.update(exit=str(exit), pnl=str(pnl), status="CLOSED - stop hit")
    return r
def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trade_log.FIELDS); w.writeheader(); w.writerows(rows)
def fills(path, rows):
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
def fill(tid, entry, exit, qty=65, source="rule"):
    return {"trade_id": tid, "index": "NIFTY", "source": source, "qty": qty, "entry_avg": entry, "paper_entry": None, "exit_avg": exit,
            "exit_qty_priced": qty, "sold_outside_qty": 0, "gross_pnl": round((exit - entry) * qty, 2)}

print("1. THE LOG'S LIVE TOTAL")
LOG = os.path.join(tempfile.mkdtemp(), "trades.csv")
write(LOG, [row("L1", "OPEN", 147.0), row("L1", "CLOSE", 147.0, 118.0, -1885.0),
            row("L2", "OPEN", 100.0), row("L2", "CLOSE", 100.0, 120.0, 1300.0),                     # live, closed today, a win
            row("P1", "OPEN", 90.0), row("P1", "CLOSE", 90.0, 95.0, 325.0),                        # paper: placed no order
            row("Y1", "OPEN", 100.0, date=YEST), row("Y1", "CLOSE", 100.0, 110.0, 650.0, date=YEST)])   # live, but yesterday
fills(LOG + ".live.fills.jsonl", [fill("L1", 147.2, 117.75), fill("L2", 101.0, 121.0), fill("Y1", 100.5, 111.0)])
net, n = trade_log.live_booked_today(TODAY, LOG)
check("only today's closed LIVE trades, at their real prices: (117.75 - 147.2) x 65 + (121 - 101) x 65", n == 2 and net == round((117.75 - 147.2) * 65 + (121 - 101) * 65, 2), (net, n))
check("the paper trade and yesterday's are not in it", trade_log.live_booked_today(YEST, LOG)[1] == 1 and trade_log.live_booked_today("2020-01-01", LOG) == (0.0, 0))
check("no log, no fills: nothing", trade_log.live_booked_today(TODAY, os.path.join(tempfile.mkdtemp(), "none.csv")) == (0.0, 0))

print("2. THE FEED'S FIGURE")
class Ex:
    def __init__(self, fills): self.fills = fills
    def real_entry(self, tid): return self.fills.get(tid)
f = feeds.Feed("t:lp", "lp@example.invalid", "nse_index")
write(f.tickets.path, [row("L1", "OPEN", 147.0), row("L1", "CLOSE", 147.0, 118.0, -1885.0)])
fills(f.tickets.path + ".live.fills.jsonl", [fill("L1", 147.2, 117.75)])
def tpub(tid, pnl, real):
    t = {"open": True, "trade_id": tid, "tracked_on": "premium", "entry": 130.0, "now": 150.0, "lots": 1, "lot_size": 65, "pnl": pnl}
    if real: t.update(entry_real=True, entry_venue="Zerodha")
    return {"ticket": t}
ai_tk = {"open": True, "trade_id": "AI-1", "tracked_on": "premium", "entry": 90.0, "now": 100.0, "lots": 1, "lot_size": 65, "pnl": 650.0}
f.live = Ex({"AI-1": (92.0, "Zerodha")})
f.ai = type("A", (), {"book": type("B", (), {"path": os.path.join(os.path.dirname(f.tickets.path), "ai_trades.csv"),
                                              "public": staticmethod(lambda name: {"ticket": dict(ai_tk)} if name == "NIFTY" else {"ticket": None})})()})()
lp = f._live_pnl({"NIFTY": tpub("R-1", 1300.0, True), "BANKNIFTY": tpub("R-2", 500.0, False), "SENSEX": {"ticket": None}})
check("booked is what closed live today", lp["booked"] == round((117.75 - 147.2) * 65, 2) and lp["closed"] == 1, lp)
check("open (the rule tickets) counts only a ticket with a real position: 1300, not 1300 + 500", lp["open"] == 1300.0 and lp["open_n"] == 1, lp)
check("the AI desk's open ticket is added, worked from ITS fill: (100 - 92) x 65", lp["ai_open"] == 520.0 and lp["ai_open_n"] == 1, lp)
check("net is all three", lp["net"] == round(lp["booked"] + 1300.0 + 520.0, 2) and lp["venue"] == "Zerodha", lp)
g = feeds.Feed("t:lp2", "lp2@example.invalid", "nse_index")
g.live = Ex({}); g.ai = None
check("no live activity today (a paper ticket only): None, so a page with none shows nothing", g._live_pnl({"NIFTY": tpub("R-2", 500.0, False)}) is None)
snap = f.snapshot()
check("the feed's snapshot carries it (this is what the state payload hands the page)", "live_pnl" in snap, list(snap)[:3])
f.live = Ex({}); f.ai = None
write(f.tickets.path, [row("L1", "OPEN", 147.0), row("L1", "CLOSE", 147.0, 118.0, -1885.0)])
snap = f.snapshot()
check("...and it is the figure, not a placeholder: today's closed live trade is in it", snap["live_pnl"] and snap["live_pnl"]["closed"] == 1 and snap["live_pnl"]["booked"] == round((117.75 - 147.2) * 65, 2), snap["live_pnl"])
f.live = None
check("a market with no live orders: None", f._live_pnl({}) is None)

print("3. THE JOURNAL")
EMAIL, MARKET = "lp@example.invalid", "nse_index"
jl = trade_log.user_log_path(EMAIL, MARKET)
write(jl, [row("L1", "OPEN", 147.0), row("L1", "CLOSE", 147.0, 118.0, -1885.0), row("P1", "OPEN", 90.0), row("P1", "CLOSE", 90.0, 95.0, 325.0)])
fills(jl + ".live.fills.jsonl", [fill("L1", 147.2, 117.75)])
allv = {e["id"]: e for e in journal.entries(EMAIL, MARKET, "all")}
check("every tool line says whether it was a real order: the venue, or None", allv["L1"]["live"] == "zerodha" and allv["P1"]["live"] is None, (allv["L1"]["live"], allv["P1"]["live"]))
check("a live line carries the real entry, exit and result", allv["L1"]["entry"] == 147.2 and allv["L1"]["exit"] == 117.75 and allv["L1"]["gross"] == round((117.75 - 147.2) * 65, 2))
live_only = journal.entries(EMAIL, MARKET, "live")
check("the 'live' filter is the trades that placed real orders, and nothing else", [e["id"] for e in live_only] == ["L1"], [e["id"] for e in live_only])
check("'tool' still has both", {e["id"] for e in journal.entries(EMAIL, MARKET, "tool")} == {"L1", "P1"})

print("4. THE PAGE")
SRC = open(os.path.join(HERE, "web_server.py")).read()
check("the state carries it, and the Journal API accepts the new filter",
      '"live_pnl": snap.get("live_pnl")' in SRC and 'source not in ("all", "mine", "tool", "ai", "live")' in SRC)
check("the Journal has the filter and a Live tag; the Today box, the Dashboard and Home show the figure",
      'data-src="live">Live orders</button>' in SRC and 'class="jbadge live"' in SRC and 'row("Live orders",' in SRC
      and "live orders <b" in SRC and 'cell("Live orders", money(livePnl(s).net)' in SRC)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if NODE:
    a = SRC.index("function livePnl(s){"); b = SRC.index("function kiteSide(s){")
    prog = """
const assert = require("assert");
const money = v => (v >= 0 ? "+" : "-") + "$" + Math.abs(Math.round(v));
""" + SRC[a:b] + r"""
assert.strictEqual(livePnl(null), null); assert.strictEqual(livePnl({}), null); assert.strictEqual(livePnl({live_pnl: null}), null);
const s = {live_pnl: {booked: -100, closed: 2, ai_open: 20, ai_open_n: 1, venue: "Delta"},
           tickets: {BTC: {ticket: {open: true, entry_real: true, pnl: 30}}, ETH: {ticket: {open: true, pnl: 999}}, X: {ticket: null}, Y: {ticket: {open: false, entry_real: true, pnl: 5}}}};
let lv = livePnl(s);
assert.strictEqual(lv.open, 50, "the rule ticket with a real position (30) + the AI's (20); the paper one and the closed one are not in it");
assert.strictEqual(lv.open_n, 2); assert.strictEqual(lv.net, -50); assert.strictEqual(lv.booked, -100); assert.strictEqual(lv.closed, 2);
s.tickets.BTC.ticket.pnl = 130; lv = livePnl(s);
assert.strictEqual(lv.net, 50, "the open part follows the tickets, which the fast tick moves");
assert.ok(liveNote(lv).includes("2 closed") && liveNote(lv).includes("2 open") && liveNote(lv).includes("-$100"), liveNote(lv));
console.log("ok:livepnl");
"""
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    print(r.stdout.strip()[-200:], r.stderr.strip()[-500:])
    check("the page's sum of live results, run in node", r.returncode == 0 and "ok:livepnl" in r.stdout)
print("LIVE PNL TEST PASSED" if not fails else f"LIVE PNL TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
