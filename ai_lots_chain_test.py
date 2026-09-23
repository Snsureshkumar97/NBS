#!/usr/bin/env python3
"""The AI desk's lot size, all the way to the quantity of a real order.

Asked for by the user on 23 Sep 2026, right after the rule tickets' lot size
was found to reset on every restart: check that the AI trades follow their lots
too. The chain is: the desk's own lots (or the rule tickets' until one is
chosen) -> frozen into the AI ticket when it opens -> the ticket engine's
"opened" event -> the live-order executor under source "ai" -> lots x the
lot size Zerodha lists. Real AIDesk, real TicketBooks on a temporary disk, the
real executor - and a fake Zerodha. Nothing here can reach an account.
"""
import datetime as dt
import os
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ai_desk as ad
import config
import live_orders as lo
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T = {"now": dt.datetime(2026, 9, 22, 11, 0, 0, tzinfo=IST), "clock": 1_789_000_000.0}
tickets.now_ist = lambda: T["now"]
EXP = "2026-09-22"
LOT = 65


def chain(spot=25010.0, ce=130.0, pe=120.0):
    strikes = []
    for k in range(24500, 25550, 50):
        c, p = ce + (25000 - k) * 0.4, pe - (25000 - k) * 0.4
        strikes.append({"strike": float(k), "call_ltp": round(max(c, 1), 2), "put_ltp": round(max(p, 1), 2),
                        "call_bid": round(max(c, 1) - 0.25, 2), "call_ask": round(max(c, 1) + 0.25, 2),
                        "put_bid": round(max(p, 1) - 0.25, 2), "put_ask": round(max(p, 1) + 0.25, 2),
                        "call_oi": 1000, "put_oi": 900})
    return {"available": True, "expiry": EXP, "spot": spot, "strikes": strikes, "pcr": 0.9}


REC = {"index": "NIFTY", "spot": 25010.0, "bias": "BULLISH", "option_type": "CE", "confidence": "Medium",
       "suggested_strike": 25000, "option_chain": chain(), "technical": {"adx": 24.0}}


def plan(strike=25000.0):
    return {"side": "CE", "strike": strike, "target": 160.0, "stop": 110.0, "ltp": 130.0, "expiry": EXP,
            "contract": f"NIFTY|{strike:g}|CE|{EXP}", "rr": 1.5}


class Streamer:
    def price(self, tok):
        return None


class FakeFeed:
    """Just what the desk reads. The rule tickets' book is the REAL one, on disk,
    because whether it remembers its lots is half of what is being checked."""
    def __init__(self, email):
        self.market, self.email, self.key = "nse_index", email, f"{email}#nse_index"
        self.lock = threading.RLock()
        self.tickets = tickets.TicketBook(owner=email, market="nse_index")
        self.streamer, self.dstream, self.faults = Streamer(), None, []
        self.live_at = __import__("time").time()
        self.state = {"feed": "ok", "indices": {n: {"rec": REC} for n in config.instruments_in("nse_index")}}
    def instruments(self):
        return config.instruments_in("nse_index")
    def snapshot(self):
        return {"indices": {}, "tickets": {}, "session": {}}
    def _note_fault(self, where, text):
        self.faults.append((where, text))


class FakeKite:
    """Enough of Kite Connect to take a buy."""
    def __init__(self):
        self.calls, self.orders_, self.n = [], {}, 0
    def margins(self, segment=None):
        return {"enabled": True, "net": 10_000_000.0, "available": {"live_balance": 10_000_000.0, "cash": 10_000_000.0}}
    def instruments(self, exch):
        rows = []
        if exch == "NFO":
            for k in ("CE", "PE"):
                rows.append({"name": "NIFTY", "instrument_type": k, "expiry": dt.date.fromisoformat(EXP),
                             "strike": 25000.0, "lot_size": LOT, "tick_size": 0.05,
                             "tradingsymbol": f"NIFTY{EXP.replace('-', '')}25000{k}"})
        return rows
    def ltp(self, keys):
        return {k: {"last_price": 130.0} for k in keys}
    def place_order(self, **kw):
        self.calls.append(("place", kw))
        self.n += 1
        oid = str(1000 + self.n)
        self.orders_[oid] = dict(kw, order_id=oid, status="OPEN", filled_quantity=0, average_price=0, status_message=None)
        return oid
    def order_history(self, oid):
        o = self.orders_[oid]
        return [{k: o[k] for k in ("order_id", "status", "filled_quantity", "average_price", "status_message")}]
    def cancel_order(self, variety, order_id):
        self.orders_[order_id]["status"] = "CANCELLED"
    def modify_order(self, variety, order_id, price=None, **kw):
        pass
    def orders(self):
        return [dict(o) for o in self.orders_.values()]
    def positions(self):
        return {"net": []}
    def buys(self):
        return [kw for c, kw in self.calls if c == "place" and kw.get("transaction_type") == "BUY"]


class Clock:
    t = 1_789_000_000.0
    now = dt.datetime(2026, 9, 22, 11, 0, tzinfo=IST)


N = [0]


def rig(email=None, feed=None):
    """A desk over a feed, and a real executor with the AI switch on, wired the
    way feeds.py wires them: the desk's book tells the executor as source ai."""
    N[0] += 1
    f = feed or FakeFeed(email or f"lots{N[0]}@example.invalid")
    d = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
    fk = FakeKite()
    ex = lo.Executor(f.email, os.path.join(tempfile.mkdtemp(), "trades.csv"), kite=lambda: fk,
                     close_ticket=lambda i, s: None, now=lambda: Clock.now, clock=lambda: Clock.t, start=False)
    ex.set_enabled("NIFTY", True, "ai")
    d.book.listeners.append(lambda kind, trade: ex.on_ticket_event(kind, trade, source="ai"))
    drain(ex)
    return f, d, ex, fk


def drain(ex):
    while not ex.q.empty():
        ex.handle(*ex.q.get())
    ex.poll()


def open_and_send(d, ex, strike=25000.0):
    d._open("NIFTY", REC, plan(strike), "a test entry")
    drain(ex)
    return d._open_trade("NIFTY")


print("1. UNTIL THE DESK HAS ITS OWN SIZE, IT FOLLOWS THE RULE TICKETS'")
f, d, ex, fk = rig()
f.tickets.configure(lots=2)
check("the desk reads the rules' lots when it has none of its own", d.lots == 2.0 and d._lots is None, (d.lots, d._lots))
t = open_and_send(d, ex)
check("the AI ticket is opened for the rules' 2 lots", t["lots"] == 2.0, t["lots"])
check("and the real order is 2 lots x the lot size Zerodha lists", [b["quantity"] for b in fk.buys()] == [2 * LOT], fk.buys())

print("2. ITS OWN SIZE OVERRIDES THE RULES' - WHICHEVER WAY THEY DIFFER")
f, d, ex, fk = rig()
f.tickets.configure(lots=2)
d.set_lots(4)
t = open_and_send(d, ex)
check("the desk's 4 lots beat the rules' 2 on the ticket", t["lots"] == 4.0, t["lots"])
check("...and on the real order", [b["quantity"] for b in fk.buys()] == [4 * LOT], fk.buys())
f, d, ex, fk = rig()
f.tickets.configure(lots=5)
d.set_lots(1)
t = open_and_send(d, ex)
check("a desk set smaller than the rules' size is not pushed up to it (1, not 5)",
      t["lots"] == 1.0 and [b["quantity"] for b in fk.buys()] == [LOT], (t["lots"], fk.buys()))

print("3. HANDING IT BACK TO THE RULES")
f, d, ex, fk = rig()
f.tickets.configure(lots=3)
d.set_lots(5)
d.set_lots(None)
check("clearing the desk's size makes it follow the rules again", d.lots == 3.0 and d._lots is None, d.lots)
t = open_and_send(d, ex)
check("and the next real order follows the rules' size", [b["quantity"] for b in fk.buys()] == [3 * LOT], fk.buys())

print("4. AN OPEN POSITION KEEPS THE SIZE IT WAS OPENED WITH")
f, d, ex, fk = rig()
d.set_lots(4)
t = open_and_send(d, ex)
d.set_lots(1)
f.tickets.configure(lots=2)
check("changing either setting afterwards leaves the open ticket at 4 lots", d._open_trade("NIFTY")["lots"] == 4.0)
pos = next(iter(ex.positions.values()))
check("...and its real position at 4 x the lot size, not resized",
      pos["lots"] == 4.0 and pos["qty"] == 4 * LOT and len(fk.buys()) == 1, (pos["lots"], pos["qty"]))

print("5. THE DESK'S OWN SIZE SURVIVES A RESTART")
f = FakeFeed("lotspersist@example.invalid")
d = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
d.set_lots(3)
d2 = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
check("a desk built again over the same account has the size that was chosen", d2._lots == 3 and d2.lots == 3, d2._lots)
d2.set_lots(None)
d3 = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
check("...and 'follow the rules' survives too, rather than snapping back to the last number", d3._lots is None)

print("6. AND SO DO THE RULES', WHICH IS WHAT A FOLLOWING DESK RESTS ON")
f = FakeFeed("lotsrules@example.invalid")
f.tickets.configure(lots=4)
d = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
check("before the restart a following desk is at 4", d.lots == 4.0)
f_after = FakeFeed("lotsrules@example.invalid")          # the process restarts: every object is new, the disk is not
d_after = ad.AIDesk(f_after, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
check("after it, the rule tickets are still at 4 and so is a following desk", f_after.tickets.lots == 4.0 and d_after.lots == 4.0,
      (f_after.tickets.lots, d_after.lots))
f_again, d_again, ex_again, fk_again = rig(feed=f_after)
open_and_send(d_again, ex_again)
check("and the first real AI order after the restart is 4 lots x the lot size, not the default one lot",
      [b["quantity"] for b in fk_again.buys()] == [4 * LOT], fk_again.buys())

print("7. THE PAGE HANDS THE SERVER ITS OWN CHOICE, AND SAYS SO WHEN IT FAILS")
src = open(os.path.join(HERE, "web_server.py")).read()
check("the AI tab's selector is rebuilt from the server's own figure every render, so it cannot show a size the server lacks",
      'const cur = d.lots;' in src and "same as rules (${esc(String(cur))})" in src)
check("...and a refused or failed change is an alert, not silence",
      'if(!j.ok) alert(j.message || "That could not be changed.");' in src)
check("the desk's size reaches the ticket engine through the one place it is applied, before every entry",
      "self.book.lots = self.lots" in open(os.path.join(HERE, "ai_desk.py")).read())

print("AI LOTS CHAIN TEST PASSED" if not fails else f"AI LOTS CHAIN TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
