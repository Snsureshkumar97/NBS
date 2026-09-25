#!/usr/bin/env python3
"""A live order's own fill price is the ticket's Entry on the screen (25 Sep 2026).

The user: "when its a live order let the tool give the entry ltp - whatever Zerodha or Delta have entered, the same ltp
should be shown in the tool". The ticket the PAGE is sent carries the executor's average fill price as `entry` (the tool's
own price kept as `entry_signal`) and its result re-worked from it; the ticket in the book, its frozen levels and the logs
are not touched. Fakes only - no broker, no account.
"""
import datetime as dt, json, os, shutil, subprocess, sys, tempfile, threading, types

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config, delta_orders, feeds, live_orders, real_entry, tickets, trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

class Ex:
    def __init__(self, fill=None, venue="Zerodha", boom=False): self.fill, self.venue, self.boom = fill, venue, boom
    def real_entry(self, tid):
        if self.boom: raise RuntimeError("socket")
        return (self.fill, self.venue) if self.fill is not None else None
def tk(**kw):
    t = {"open": True, "trade_id": "NIFTY-20260925-140535", "tracked_on": "premium", "entry": 130.0, "now": 150.0,
         "lots": 2, "lot_size": 65, "pnl": 2600.0}
    t.update(kw); return t

print("1. THE OVERLAY")
t = tk(); ok = real_entry.apply(Ex(132.6), t)
check("a fill replaces the entry, and the tool's own price is kept beside it", ok and t["entry"] == 132.6 and t["entry_signal"] == 130.0 and t["entry_real"] is True, t)
check("the venue is named", t["entry_venue"] == "Zerodha")
check("the result is re-worked from the fill: (150 - 132.6) x 65 x 2", t["pnl"] == round((150 - 132.6) * 65 * 2, 2), t["pnl"])
t = tk(entry=574.0, now=600.0, lots=25, lot_size=0.001, pnl=round((600 - 574.0) * 0.001 * 25, 2)); real_entry.apply(Ex(588.3, "Delta"), t)
check("Bitcoin, the 25 Sep case (signal 574.0, filled 588.3): the same rule", t["entry"] == 588.3 and t["pnl"] == round((600 - 588.3) * 0.001 * 25, 2), t)
for name, args in (("no executor", (None, tk())), ("no fill yet", (Ex(None), tk())), ("a ticket that is not open", (Ex(132.6), tk(open=False))),
                   ("a ticket tracked on the index", (Ex(132.6), tk(tracked_on="index"))), ("no trade id", (Ex(132.6), tk(trade_id=None))),
                   ("an executor that raises", (Ex(boom=True), tk())), ("no ticket", (Ex(132.6), None)), ("no entry to compare with", (Ex(132.6), tk(entry=None)))):
    t = args[1]; before = json.dumps(t, sort_keys=True); ok = real_entry.apply(args[0], t)
    check(f"{name}: nothing changes", ok is False and json.dumps(t, sort_keys=True) == before)
t = tk(pnl=None); real_entry.apply(Ex(132.6), t)
check("no result yet (no price): the entry still moves, the result stays empty", t["entry"] == 132.6 and t["pnl"] is None)

print("2. THE EXECUTORS ANSWER ONLY WHEN SOMETHING HAS FILLED")
for mod, venue in ((live_orders, "Zerodha"), (delta_orders, "Delta")):
    e = mod.Executor.__new__(mod.Executor); e.lock = threading.RLock()
    e.positions = {"a": {"avg_price": None, "filled_qty": 0}, "b": {"avg_price": 132.6, "filled_qty": 0},
                   "c": {"avg_price": 132.6, "filled_qty": 130}, "d": {"avg_price": "147.2", "filled_qty": 50}}
    check(f"[{venue}] an unknown trade: None", e.real_entry("zzz") is None)
    check(f"[{venue}] placed but nothing filled: None", e.real_entry("a") is None and e.real_entry("b") is None)
    check(f"[{venue}] filled: the average price and the venue", e.real_entry("c") == (132.6, venue), e.real_entry("c"))
    check(f"[{venue}] a price that arrives as text is a number", e.real_entry("d") == (147.2, venue))

print("3. THE FEED SENDS THE PAGE THE FILL, AND ONLY THE PAGE'S COPY CHANGES")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = dt.datetime(2026, 9, 8, 11, 0, 0, tzinfo=IST); CLOCK = {"s": 0.0}
tickets.now_ist = lambda: BASE + dt.timedelta(seconds=CLOCK["s"])
tickets.time = types.SimpleNamespace(time=lambda: 1_780_000_000 + CLOCK["s"])
tickets.is_market_open = lambda *a, **k: True
tickets._safe_explain = lambda rec: {"verdict": "test"}
trade_log._log_path = lambda: os.path.join(tempfile.mkdtemp(), "trades.csv")
book = tickets.TicketBook(owner=None, market="nse_index")
rec = {"index": "NIFTY", "bias": "BULLISH", "option_type": "CE", "suggested_strike": 24500, "spot": 24500.0,
       "index_targets": [24560., 24610., 24660.], "index_stop_loss": 24440., "premium_targets": [145., 154., 160.],
       "premium_stop_loss": 110., "premium_source": "live", "live_ltp": 130.0, "ltp": 130.0, "lot_size": 65,
       "option_chain": None, "risk_points": 60.0, "reach_points": 160.0, "reach_to_risk": 2.67,
       "opening_range": {"ready": True, "high": 24400.0, "low": 24300.0}, "spread": None, "technical": {"adx": 30}}
opened = []
for _ in range(130):
    CLOCK["s"] += 1
    opened += [e for e in book.update("NIFTY", dict(rec)) if e.get("kind") == "opened"]
tr = book.books["NIFTY"].trade
check("a ticket opened, and its public form carries its trade id", tr is not None and book.public("NIFTY")["ticket"]["trade_id"] == tr["trade_id"])
f = feeds.Feed.__new__(feeds.Feed)
f.tickets = book; f.state = {"indices": {"NIFTY": {"public": {}}}}
f.live = Ex(None)
pub = f._tickets_with_odds()["NIFTY"]["ticket"]
paper = pub["entry"]
check("no live order: the tool's own entry, no fill marker", pub.get("entry_real") is None and "entry_signal" not in pub, pub.get("entry"))
f.live = Ex(paper + 2.6, "Zerodha")
pub = f._tickets_with_odds()["NIFTY"]["ticket"]
check("a live fill: the page's entry is the broker's", pub["entry"] == round(paper + 2.6, 10) and pub["entry_signal"] == paper and pub["entry_venue"] == "Zerodha", pub["entry"])
seen = []
real_tc = feeds._trade_charges
feeds._trade_charges = lambda k, entry, levels, lot: (seen.append(entry), real_tc(k, entry, levels, lot))[1]
f._tickets_with_odds()
feeds._trade_charges = real_tc
check("the charges (and so the risk and cost figures) are worked from the price actually paid", seen and seen[-1] == pub["entry"], seen)
check("the ticket in the book keeps the tool's price: its levels and its log are unchanged", book.books["NIFTY"].trade["entry_ltp"] == paper and book.books["NIFTY"].trade["premium_sl"] == 110.0)
check("and asking again does not compound the change", f._tickets_with_odds()["NIFTY"]["ticket"]["entry"] == pub["entry"])

print("4. THE PAGE")
SRC = open(os.path.join(HERE, "web_server.py")).read()
AI = open(os.path.join(HERE, "ai_desk.py")).read()
check("the AI desk's open ticket, the option chart's Entry line and the snapshot all go through the same overlay",
      "real_entry.apply(getattr(self.feed, \"live\", None), t)" in AI and "real_entry.apply(getattr(feed, \"live\", None), t)" in SRC
      and "real_entry.apply(getattr(self, \"live\", None), t)" in open(os.path.join(HERE, "feeds.py")).read())
check("the Signal ticket row, the left column and the AI card say 'filled' when it is",
      'cell(entryLabel(tk), num(tk.entry,dp))' in SRC and 'row(tk.entry_real ? "Entry (filled)" : "Entry", num(tk.entry))' in SRC
      and 'cell(entryLabel(t), num(t.entry, dp))' in SRC and "entryNote(tk, " in SRC and "entryNote(t, dp)" in SRC)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if NODE:
    a = SRC.index("const entryLabel = t =>"); b = SRC.index("function ticketBox(r, state){")
    prog = """
const assert = require("assert");
const num = (v, d) => v == null ? "—" : Number(v).toFixed(d);
""" + SRC[a:b] + r"""
assert.strictEqual(entryLabel({entry_real: true}), "Entry · filled");
assert.strictEqual(entryLabel({}), "Entry"); assert.strictEqual(entryLabel(null), "Entry");
assert.strictEqual(entryNote({}, 2), ""); assert.strictEqual(entryNote(null, 2), "");
const n = entryNote({entry_real: true, entry_venue: "Delta", entry: 588.3, entry_signal: 574.0}, 2);
assert.ok(n.includes("filled at Delta for 588.30") && n.includes("the tool's price was 574.00"), n);
console.log("ok:entrynote");
"""
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    print(r.stdout.strip()[-200:], r.stderr.strip()[-600:])
    check("the label and the note read as intended - run in node", r.returncode == 0 and "ok:entrynote" in r.stdout)
print("REAL ENTRY TEST PASSED" if not fails else f"REAL ENTRY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
