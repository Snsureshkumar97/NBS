#!/usr/bin/env python3
"""The lot size picked on the Signal page survives a restart.

Reported by the user on 23 Sep 2026: "when I change the lots it does not change
on live orders". The rule tickets' lots lived only in memory (TicketBook.lots),
so every restart - four of them that day - silently put it back to the smallest
size while an open page kept showing the chosen number. Every ticket and every
real order that day was exactly one lot. Fakes only - a temporary log folder.
"""
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def book(path, market="nse_index"):
    return tickets.TicketBook(owner=None, market=market, path=path)


def fresh_path():
    return os.path.join(tempfile.mkdtemp(), "trades.csv")


print("1. THE CHOSEN SIZE COMES BACK AFTER A RESTART")
p = fresh_path()
b = book(p)
check("a new book starts at the smallest size the market offers", b.lots == config.lot_choices("nse_index")[0])
b.configure(lots=3)
check("configure sets it", b.lots == 3.0)
b2 = book(p)                                   # the process restarts: a new book, same log
check("a book built again over the same log has the size that was chosen, not the default", b2.lots == 3.0, b2.lots)
b2.configure(lots=5)
check("...and it can be changed again and comes back again", book(p).lots == 5.0)

print("2. IT SITS BESIDE THE ACCOUNT SETTINGS WITHOUT CLOBBERING THEM")
p = fresh_path()
b = book(p)
b.configure(capital=250000, risk_pct=1.5)
b.configure(lots=2)
b2 = book(p)
check("setting the lots later does not lose the capital or the risk share",
      b2.capital == 250000.0 and b2.risk_pct == 1.5 and b2.lots == 2.0, (b2.capital, b2.risk_pct, b2.lots))
b2.configure(capital=300000)
check("...and setting the capital later does not lose the lots", book(p).lots == 2.0 and book(p).capital == 300000.0)

print("3. A SAVED SIZE THE MARKET NO LONGER OFFERS IS SNAPPED, NOT TRUSTED")
p = fresh_path()
with open(p + ".settings.json", "w") as fh:
    json.dump({"capital": None, "risk_pct": 1.0, "lots": 9}, fh)
check("a size beyond the largest choice becomes the largest", book(p).lots == 5.0)
with open(p + ".settings.json", "w") as fh:
    json.dump({"lots": 2.4}, fh)
check("one between two choices becomes the nearest", book(p).lots == 2.0)

print("4. NOTHING SAVED, OR A DAMAGED FILE, FALLS BACK TO THE DEFAULT")
p = fresh_path()
check("no settings file at all: the default", book(p).lots == 1.0)
with open(p + ".settings.json", "w") as fh:
    fh.write("not json")
check("a damaged settings file: the default, no crash", book(p).lots == 1.0)
with open(p + ".settings.json", "w") as fh:
    json.dump({"lots": None}, fh)
check("a null size: the default", book(p).lots == 1.0)

print("5. EACH MARKET KEEPS ITS OWN SIZE, ON ITS OWN SCALE")
pc = fresh_path()
bc = book(pc, "crypto")
bc.configure(lots=50)
check("Bitcoin's contract count survives a restart too", book(pc, "crypto").lots == 50.0, book(pc, "crypto").lots)
check("...and an Indian-index book over another log is unaffected", book(fresh_path()).lots == 1.0)

print("6. THE SHARED BOOK (NO LOG, NO FILE) STILL WORKS")
bs = tickets.TicketBook(owner=None, market="nse_index", path=None)
bs.configure(lots=4)
check("no path to save to: the size is kept in memory and nothing crashes", bs.lots == 4.0)

print("7. THE AI DESK'S OWN BOOK CANNOT BE PUSHED OFF ITS OWN SIZE BY THIS")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_desk.py")).read()
check("the desk still sets its book's lots itself before every entry, whatever a file says",
      "self.book.lots = self.lots" in src)

print("LOTS PERSIST TEST PASSED" if not fails else f"LOTS PERSIST TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
