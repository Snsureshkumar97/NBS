#!/usr/bin/env python3
"""The Journal and the Record show what a live order really filled at (25 Sep 2026).

The user said yes to "the filled price written into the trade log as well". The log file is append-only and stays as written
(the tool's own prices, the record of what it decided); trade_log._read_rows - the one door every screen, the session totals,
the daily loss limit and the AI desk's record read it through - hands each closed LIVE trade over with its real entry, exit
and result from the executor's fills log, and hands everything else over as written. Fakes only: a temp log and temp fills.
"""
import csv, json, os, sys, tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

D = tempfile.mkdtemp()
LOG = os.path.join(D, "trades.csv")
AILOG = os.path.join(D, "ai_trades.csv")

def row(tid, event, entry, exit=None, pnl=None, lots=1, lot_size=65, status="OPEN"):
    r = {k: "" for k in trade_log.FIELDS}
    r.update(trade_id=tid, event=event, date="2026-09-25", time_ist="14:05:35", index="NIFTY", strike="23150", option_type="CE",
             entry=str(entry), lot_size=str(lot_size), lots=str(lots), status=status)
    if event == "CLOSE":
        r.update(exit=str(exit), pnl=str(pnl), status="CLOSED - stop hit")
    return r
def write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trade_log.FIELDS); w.writeheader(); w.writerows(rows)
def fills(path, rows):
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
def fill(tid, entry, exit, qty=65, priced=None, outside=0, source="rule", gross="auto"):
    priced = qty if priced is None else priced
    return {"trade_id": tid, "index": "NIFTY", "source": source, "qty": qty, "entry_avg": entry, "paper_entry": None,
            "exit_avg": exit, "exit_qty_priced": priced, "sold_outside_qty": outside,
            "gross_pnl": round((exit - entry) * priced, 2) if (exit is not None and gross == "auto") else gross if gross != "auto" else None}

print("1. A CLOSED LIVE TRADE READS AS WHAT IT FILLED AT")
write(LOG, [row("T1", "OPEN", 147.0), row("T1", "CLOSE", 147.0, 118.5, round((118.5 - 147.0) * 65, 2)),
            row("T2", "OPEN", 100.0), row("T2", "CLOSE", 100.0, 110.0, 650.0)])
fills(LOG + ".live.fills.jsonl", [fill("T1", 147.2, 117.75)])
rows = trade_log._read_rows(LOG)
o1, c1 = [r for r in rows if r["trade_id"] == "T1" and r["event"] == "OPEN"][0], [r for r in rows if r["trade_id"] == "T1" and r["event"] == "CLOSE"][0]
check("the OPEN row's entry is the fill", float(o1["entry"]) == 147.2 and float(o1["paper_entry"]) == 147.0, o1["entry"])
check("the CLOSE row: real entry, real exit, real result", float(c1["entry"]) == 147.2 and float(c1["exit"]) == 117.75
      and float(c1["pnl"]) == round((117.75 - 147.2) * 65, 2), (c1["entry"], c1["exit"], c1["pnl"]))
check("the tool's own numbers ride along, and the venue is named", float(c1["paper_pnl"]) == round((118.5 - 147.0) * 65, 2) and float(c1["paper_exit"]) == 118.5 and c1["filled"] == "zerodha")
t2 = [r for r in rows if r["trade_id"] == "T2"]
check("a trade with no fill is handed over as written", all(r["entry"] == "100.0" and "filled" not in r for r in t2))
check("the file itself is untouched: the tool's own prices, and no fills columns", "147.2" not in open(LOG).read() and "paper_entry" not in open(LOG).read())
check("the numbers add up through the session totals (booked today is the real result)",
      abs(sum(float(r["pnl"]) for r in rows if r["event"] == "CLOSE") - (round((117.75 - 147.2) * 65, 2) + 650.0)) < 0.01)

print("2. ONLY A WHOLE, CONSISTENT FILL IS USED")
def case(name, f, lots=1, lot_size=65, expect_changed=False, venue_file=".live.fills.jsonl"):
    write(LOG, [row("X", "OPEN", 100.0, lots=lots, lot_size=lot_size), row("X", "CLOSE", 100.0, 110.0, 650.0, lots=lots, lot_size=lot_size)])
    for suf in (".live.fills.jsonl", ".delta.fills.jsonl"):
        if os.path.exists(LOG + suf): os.remove(LOG + suf)
    fills(LOG + venue_file, [f])
    r = [x for x in trade_log._read_rows(LOG) if x["event"] == "CLOSE"][0]
    check(name, (r["entry"] != "100.0") == expect_changed, (r["entry"], r["exit"], r["pnl"]))
case("a whole fill is used", fill("X", 101.0, 111.0), expect_changed=True)
case("not everything bought was sold at a known price: as written", fill("X", 101.0, 111.0, priced=30))
case("some was sold outside the tool: as written", fill("X", 101.0, 111.0, outside=5))
case("no exit price: as written", fill("X", 101.0, None))
case("no result: as written", dict(fill("X", 101.0, 111.0), gross_pnl=None))
case("the size is not the ticket's (1 lot of 65, 40 bought): as written", fill("X", 101.0, 111.0, qty=40))
case("garbage in the fill: as written", dict(fill("X", 101.0, 111.0), qty="lots"))
case("Delta counts contracts: 50 contracts for lots=50 is whole", dict(fill("X", 574.0, 600.0, qty=50), gross_pnl=1.3), lots=50, lot_size=0.001, expect_changed=True, venue_file=".delta.fills.jsonl")
case("...and 50 contracts against a 1-lot ticket is not", dict(fill("X", 574.0, 600.0, qty=50), gross_pnl=1.3), lots=1, lot_size=0.001, venue_file=".delta.fills.jsonl")

print("3. THE AI DESK'S LOG, AND CLEAN EDGES")
write(AILOG, [row("A1", "OPEN", 90.0), row("A1", "CLOSE", 90.0, 99.0, 585.0), row("R1", "OPEN", 90.0), row("R1", "CLOSE", 90.0, 99.0, 585.0)])
for suf in (".live.fills.jsonl", ".delta.fills.jsonl"):
    if os.path.exists(LOG + suf): os.remove(LOG + suf)
fills(LOG + ".live.fills.jsonl", [fill("A1", 91.0, 100.0, source="ai"), fill("R1", 91.0, 100.0, source="rule")])
r = {(x["trade_id"], x["event"]): x for x in trade_log._read_rows(AILOG)}
check("the AI desk's log reads the account's fills log - and only the AI's fills", r[("A1", "CLOSE")]["entry"] == "91.0" and r[("R1", "CLOSE")]["entry"] == "90.0")
with open(LOG + ".live.fills.jsonl", "a") as f: f.write("this is not json\n[1,2]\n")
check("a bad line in the fills log is skipped, not fatal", trade_log._read_rows(AILOG) and {x["event"] for x in trade_log._read_rows(AILOG)} == {"OPEN", "CLOSE"})
check("no fills file at all: rows are exactly what was written", trade_log._read_rows(os.path.join(tempfile.mkdtemp(), "nothing.csv")) == [])
write(LOG, [row("Z", "OPEN", 100.0)]); os.remove(LOG + ".live.fills.jsonl")
check("no fills log next to a log: rows as written", trade_log._read_rows(LOG)[0]["entry"] == "100.0" and "filled" not in trade_log._read_rows(LOG)[0])
write(LOG, [row("Q", "OPEN", 100.0), row("Q", "CLOSE", 100.0, 110.0, 650.0)])
fills(LOG + ".live.fills.jsonl", [fill("Q", 101.0, 111.0)])
a = trade_log._read_rows(LOG)[1]["entry"]
fills(LOG + ".live.fills.jsonl", [fill("Q", 105.0, 111.0)])
os.utime(LOG + ".live.fills.jsonl", None)
b = trade_log._read_rows(LOG)[1]["entry"]
check("a changed fills file is read again (the cache follows the file)", a == "101.0" and b == "105.0", (a, b))

print("TRADE LOG FILLS TEST PASSED" if not fails else f"TRADE LOG FILLS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
