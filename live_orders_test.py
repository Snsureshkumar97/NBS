#!/usr/bin/env python3
"""Live Zerodha orders that follow a ticket - against a fake Zerodha only.

Nothing here can reach a real account: the executor is handed FakeKite, a
temporary log folder, and a clock the test moves by hand. What matters most:
the right contract and quantity, a stop at Zerodha the moment a buy fills, never
selling more than is held (the thing that would leave an account short an
option), the 15:20 close, and what happens on a restart.
"""
import datetime as dt
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import live_orders as lo

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
EXP = "2026-09-22"


class NetworkException(Exception):
    pass


class PermissionException(Exception):
    pass


class FakeKite:
    """Just enough of Kite Connect: orders fill only when the test says so."""

    def __init__(self):
        self.orders_ = {}
        self.calls = []
        self.last = {}
        self.n = 0
        self.raise_on_place = []         # exceptions to raise on the next place_order calls
        self.manual_sold = 0
        self.positions_fail = False

    def instruments(self, exch):
        self.calls.append(("instruments", exch))
        rows = []
        if exch == "NFO":
            for exp, lot in ((EXP, 65), ("2026-09-29", 65)):
                for k in ("CE", "PE"):
                    rows.append({"name": "NIFTY", "instrument_type": k, "expiry": dt.date.fromisoformat(exp),
                                 "strike": 25000.0, "lot_size": lot, "tick_size": 0.05,
                                 "tradingsymbol": f"NIFTY{exp.replace('-', '')}25000{k}"})
            rows.append({"name": "BANKNIFTY", "instrument_type": "CE", "expiry": dt.date(2026, 9, 29),
                         "strike": 56000.0, "lot_size": 30, "tick_size": 0.05, "tradingsymbol": "BANKNIFTY26SEP56000CE"})
        if exch == "BFO":
            rows.append({"name": "SENSEX", "instrument_type": "CE", "expiry": dt.date(2026, 9, 18),
                         "strike": 82000.0, "lot_size": 20, "tick_size": 0.05, "tradingsymbol": "SENSEX2691882000CE"})
        return rows

    def ltp(self, keys):
        self.calls.append(("ltp", keys))
        return {k: {"last_price": self.last.get(k.split(":", 1)[1], 130.0)} for k in keys}

    def place_order(self, **kw):
        self.calls.append(("place", kw))
        if self.raise_on_place:
            raise self.raise_on_place.pop(0)
        self.n += 1
        oid = str(1000 + self.n)
        status = "TRIGGER PENDING" if kw.get("order_type") == "SL" else "OPEN"
        self.orders_[oid] = dict(kw, order_id=oid, status=status, filled_quantity=0, average_price=0,
                                 status_message=None)
        return oid

    def order_history(self, oid):
        o = self.orders_[oid]
        return [{k: o[k] for k in ("order_id", "status", "filled_quantity", "average_price", "status_message")}]

    def cancel_order(self, variety, order_id):
        self.calls.append(("cancel", order_id))
        o = self.orders_[order_id]
        if o["status"] in ("COMPLETE", "REJECTED", "CANCELLED"):
            raise Exception("Order cannot be cancelled as it is being processed")
        o["status"] = "CANCELLED"

    def modify_order(self, variety, order_id, price=None, **kw):
        self.calls.append(("modify", order_id, price))
        self.orders_[order_id]["price"] = price

    def orders(self):
        return [dict(o) for o in self.orders_.values()]

    def positions(self):
        if self.positions_fail:
            raise Exception("positions down")
        if getattr(self, "positions_lag", False):
            return {"net": []}           # a fill not reflected in positions yet
        net = {}
        for o in self.orders_.values():
            sign = 1 if o["transaction_type"] == "BUY" else -1
            net[o["tradingsymbol"]] = net.get(o["tradingsymbol"], 0) + sign * o["filled_quantity"]
        return {"net": [{"tradingsymbol": s, "product": "MIS", "quantity": q - (self.manual_sold if q > 0 else 0)}
                        for s, q in net.items()]}

    # -- the market, driven by the test
    def fill(self, oid, qty=None, price=130.0):
        o = self.orders_[oid]
        o["filled_quantity"] = o["quantity"] if qty is None else qty
        o["average_price"] = price
        o["status"] = "COMPLETE" if o["filled_quantity"] >= o["quantity"] else "OPEN"

    def reject(self, oid, msg):
        self.orders_[oid].update(status="REJECTED", status_message=msg)

    def places(self, **match):
        return [kw for c, kw in ((c[0], c[1]) for c in self.calls if c[0] == "place")
                if all(kw.get(k) == v for k, v in match.items())]

    def sold_total(self):
        return sum(o["filled_quantity"] for o in self.orders_.values() if o["transaction_type"] == "SELL")


class Clock:
    def __init__(self):
        self.t = 1_789_000_000.0
        self.now = dt.datetime(2026, 9, 18, 11, 0, tzinfo=IST)

    def advance(self, s):
        self.t += s
        self.now += dt.timedelta(seconds=s)


def rig(enabled=("NIFTY",), path=None, ai=()):
    fk, clk, closed = FakeKite(), Clock(), []
    path = path or os.path.join(tempfile.mkdtemp(), "trades.csv")
    ex = lo.Executor("me@example.invalid", path, kite=lambda: fk,
                     close_ticket=lambda index, status: closed.append((index, status)),
                     now=lambda: clk.now, clock=lambda: clk.t, start=False)
    for i in enabled:
        ex.set_enabled(i, True)
    for i in ai:
        ex.set_enabled(i, True, "ai")
    drain(ex)
    return ex, fk, clk, closed


def drain(ex):
    while not ex.q.empty():
        ex.handle(*ex.q.get())
    ex.poll()


def ticket(index="NIFTY", tid=None, strike=25000, opt="CE", expiry=EXP, lots=2.0, use_premium=True,
           entry_ltp=130.0, sl=97.5, targets=(156.0, 182.0, 227.0)):
    return {"trade_id": tid or f"{index}-20260918-110000", "index": index, "strike": strike, "option_type": opt,
            "expiry": expiry, "lots": lots, "use_premium": use_premium, "entry_ltp": entry_ltp,
            "premium_sl": sl, "premium_targets": list(targets), "status": "OPEN"}


def open_and_fill(ex, fk, clk, t=None, price=130.0):
    t = t or ticket()
    ex.on_ticket_event("opened", t)
    drain(ex)
    buy = [o for o in fk.orders_.values() if o["transaction_type"] == "BUY"][-1]
    fk.fill(buy["order_id"], price=price)
    drain(ex)
    return t, buy


print("1. NOTHING HAPPENS UNLESS THE INDEX IS SWITCHED ON")
ex, fk, clk, closed = rig(enabled=())
ex.on_ticket_event("opened", ticket())
check("the ticket engine's callback only queues - no call to Zerodha from inside its lock",
      ex.q.qsize() == 1 and not fk.calls)
drain(ex)
check("switched off: no order at all", not fk.places())
check("every index starts switched off", ex.enabled == {"NIFTY": False, "BANKNIFTY": False, "SENSEX": False})
try:
    ex.set_enabled("BTC", True)
    check("an index outside the three cannot be switched on", False)
except ValueError:
    check("an index outside the three cannot be switched on", True)

print("2. THE ENTRY: RIGHT CONTRACT, RIGHT QUANTITY, A LIMIT")
ex, fk, clk, closed = rig()
fk.last["NIFTY2026092225000CE"] = 131.0
ex.on_ticket_event("opened", ticket())
drain(ex)
buys = fk.places(transaction_type="BUY")
b = buys[0] if buys else {}
check("one BUY sent", len(buys) == 1, buys)
check("the ticket's own expiry, not the next one with the same strike", b.get("tradingsymbol") == "NIFTY2026092225000CE")
_cfg_lot = config.INSTRUMENTS["NIFTY"]["lot_size"]
config.INSTRUMENTS["NIFTY"]["lot_size"] = 75
try:
    ex, fk, clk, closed = rig()
    fk.last["NIFTY2026092225000CE"] = 131.0
    ex.on_ticket_event("opened", ticket())
    drain(ex)
    q = (fk.places(transaction_type="BUY") or [{}])[0].get("quantity")
finally:
    config.INSTRUMENTS["NIFTY"]["lot_size"] = _cfg_lot
check("quantity is lots x ZERODHA's lot size (65), even if the tool's config were stale (75)",
      b.get("quantity") == 130 and q == 130, (b.get("quantity"), q))
check("the tool's own Nifty lot size now matches Zerodha's", _cfg_lot == 65)
check("intraday product, on NFO, a DAY order", b.get("product") == "MIS" and b.get("exchange") == "NFO"
      and b.get("validity") == "DAY")
check("a LIMIT 2% above the live price, rounded UP to the tick", b.get("order_type") == "LIMIT" and b.get("price") == 133.65,
      b.get("price"))
check("tagged so it can be found again", (b.get("tag") or "").startswith("TP") and len(b["tag"]) <= 20)
check("no stop yet - nothing has filled", not fk.places(order_type="SL"))

ex, fk, clk, closed = rig(enabled=("SENSEX",))
ex.on_ticket_event("opened", ticket("SENSEX", strike=82000, expiry="2026-09-18", lots=1.0))
drain(ex)
s = fk.places(transaction_type="BUY")
check("Sensex goes to BFO with its own lot size", s and s[0]["exchange"] == "BFO" and s[0]["quantity"] == 20, s)

print("3. THE FILL PUTS A STOP AT ZERODHA AT ONCE")
ex, fk, clk, closed = rig()
t, buy = open_and_fill(ex, fk, clk, price=131.2)
sl = fk.places(order_type="SL")
check("a stop-loss LIMIT sell - Zerodha takes no SL-M on index options",
      len(sl) == 1 and sl[0]["transaction_type"] == "SELL" and not fk.places(order_type="SL-M"), sl)
check("trigger is the ticket's frozen stop; limit 5% under it, rounded down to the tick",
      sl[0]["trigger_price"] == 97.5 and sl[0]["price"] == 92.6, (sl[0]["trigger_price"], sl[0]["price"]))
check("for the quantity that filled", sl[0]["quantity"] == 130)
check("state is open", ex.positions[t["trade_id"]]["state"] == "open")

print("4. T2: CANCEL THE STOP, SELL WHAT IS HELD - ONCE")
fk.last["NIFTY2026092225000CE"] = 183.0
ex.on_ticket_event("closed", dict(t, status="CLOSED — T2 hit (full target reached)"))
drain(ex)
sells = fk.places(transaction_type="SELL", order_type="LIMIT")
check("the stop order is cancelled first", ("cancel", [o for o in fk.orders_.values() if o["order_type"] == "SL"][0]["order_id"]) in fk.calls)
check("one LIMIT sell, 3% under the live price, rounded down", len(sells) == 1 and sells[0]["price"] == 177.5
      and sells[0]["quantity"] == 130, sells)
ex.on_ticket_event("closed", dict(t, status="CLOSED — T2 hit (full target reached)"))
clk.advance(1)
drain(ex)
check("a second close event while the sell is working sends nothing more",
      len(fk.places(transaction_type="SELL", order_type="LIMIT")) == 1)
fk.fill([o for o in fk.orders_.values() if o["order_type"] == "LIMIT" and o["transaction_type"] == "SELL"][0]["order_id"],
        price=182.1)
drain(ex)
p = ex.positions[t["trade_id"]]
check("filled: closed, with the exit price", p["state"] == "closed" and p["exit_price"] == 182.1, p["state"])
check("never sold more than was bought", fk.sold_total() == 130)

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
ex.on_ticket_event("closed", dict(t, status="CLOSED — cleared manually"))
drain(ex)
pos = ex.positions[t["trade_id"]]
ex._sell_rest(pos)
ex._sell_rest(pos)
check("asked to sell again while a sell is working (any path): nothing more is sent",
      len(fk.places(transaction_type="SELL", order_type="LIMIT")) == 1)

print("5. 15:20 CLOSES IT - AND THE TICKET")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
clk.now = clk.now.replace(hour=15, minute=20, second=0)
drain(ex)
check("the ticket is closed with a status that says why", closed == [("NIFTY", "CLOSED — intraday close at 15:20 (live order)")])
check("and the position is being sold", len(fk.places(transaction_type="SELL", order_type="LIMIT")) == 1)
ex.on_ticket_event("closed", dict(t, status="CLOSED — intraday close at 15:20 (live order)"))
drain(ex)
check("the close event that follows does not sell a second time",
      len(fk.places(transaction_type="SELL", order_type="LIMIT")) == 1)

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
def boom(index, status):
    raise RuntimeError("ticket engine hiccup")
ex.close_ticket = boom
clk.now = clk.now.replace(hour=15, minute=21)
drain(ex)
check("if closing the ticket fails, the position is still sold", len(fk.places(transaction_type="SELL", order_type="LIMIT")) == 1)

print("6. THE STOP FILLS AT ZERODHA")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
stop_id = [o for o in fk.orders_.values() if o["order_type"] == "SL"][0]["order_id"]
fk.fill(stop_id, price=96.9)
drain(ex)
check("closed on the exchange's own fill", ex.positions[t["trade_id"]]["state"] == "closed")
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
check("the ticket's stop close afterwards sells nothing", not fk.places(order_type="LIMIT", transaction_type="SELL")
      and fk.sold_total() == 130)

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
stop_id = [o for o in fk.orders_.values() if o["order_type"] == "SL"][0]["order_id"]
fk.orders_[stop_id]["status"] = "OPEN"                   # triggered, limit not filled
fk.last["NIFTY2026092225000CE"] = 88.0
drain(ex)
check("stop triggered but its limit unfilled: waits a moment", not fk.places(order_type="LIMIT", transaction_type="SELL"))
clk.advance(lo.REPRICE_S)
drain(ex)
sells = fk.places(order_type="LIMIT", transaction_type="SELL")
check("...then cancels it and sells below the market", len(sells) == 1 and sells[0]["price"] == 85.35
      and ("cancel", stop_id) in fk.calls, sells)

print("7. ENTRY THAT DOES NOT FILL, OR ONLY PARTLY")
ex, fk, clk, closed = rig()
ex.on_ticket_event("opened", ticket())
drain(ex)
buy = fk.places(transaction_type="BUY")
bid = [o for o in fk.orders_.values() if o["transaction_type"] == "BUY"][0]["order_id"]
clk.advance(lo.FILL_WAIT_S)
drain(ex)
p = ex.positions[ticket()["trade_id"]]
check("nothing filled in FILL_WAIT_S: cancelled, failed, no stop", ("cancel", bid) in fk.calls
      and p["state"] == "failed" and not fk.places(order_type="SL"))
check("...and said so", "not filled" in ex.notes[0]["text"])

ex, fk, clk, closed = rig()
ex.on_ticket_event("opened", ticket())
drain(ex)
bid = [o for o in fk.orders_.values() if o["transaction_type"] == "BUY"][0]["order_id"]
fk.fill(bid, qty=65, price=132.0)
clk.advance(lo.FILL_WAIT_S)
drain(ex)
sl = fk.places(order_type="SL")
check("half filled: the rest cancelled and the stop is for the half that filled",
      ("cancel", bid) in fk.calls and sl and sl[0]["quantity"] == 65, sl)

print("8. REFUSALS")
ex, fk, clk, closed = rig()
fk.raise_on_place = [PermissionException("Request IP 49.36.1.2 is not allowed for this app")]
ex.on_ticket_event("opened", ticket())
drain(ex)
p = ex.positions[ticket()["trade_id"]]
check("an unregistered IP: failed, with Zerodha's words and a pointer to the static IP",
      p["state"] == "failed" and "not allowed" in ex.notes[0]["text"] and "static IP" in ex.notes[0]["text"],
      ex.notes[0]["text"])
check("and nothing further is tried for that ticket - the one refused attempt only", len(fk.places()) == 1)

ex, fk, clk, closed = rig()
ex.on_ticket_event("opened", ticket())
drain(ex)
bid = [o for o in fk.orders_.values() if o["transaction_type"] == "BUY"][0]["order_id"]
fk.reject(bid, "Insufficient funds")
drain(ex)
check("rejected by Zerodha after sending: failed with its reason",
      ex.positions[ticket()["trade_id"]]["state"] == "failed" and "Insufficient funds" in ex.notes[0]["text"])

ex, fk, clk, closed = rig()
ex.on_ticket_event("opened", ticket())
drain(ex)
bid = [o for o in fk.orders_.values() if o["transaction_type"] == "BUY"][0]["order_id"]
fk.raise_on_place = [Exception("Trigger price invalid")]
fk.fill(bid, price=130.0)
drain(ex)
sells = fk.places(transaction_type="SELL", order_type="LIMIT")
check("the stop cannot be placed: the position is sold at once, never left unprotected",
      len(sells) == 1 and sells[0]["quantity"] == 130, sells)

ex, fk, clk, closed = rig()
fk.positions_lag = True
t, _ = open_and_fill(ex, fk, clk, price=97.0)
s = fk.places(transaction_type="SELL", order_type="LIMIT")
check("filled at or below the stop: sold at once, no stop order",
      not fk.places(order_type="SL") and len(s) == 1)
check("...for the full quantity, even though Zerodha's positions have not caught up with the fill yet",
      s and s[0]["quantity"] == 130, s)

print("9. WHAT NEVER GETS AN ORDER")
ex, fk, clk, closed = rig()
ex.on_ticket_event("opened", ticket(use_premium=False))
drain(ex)
check("a ticket priced off the index", not fk.places() and "priced off the index" in ex.notes[0]["text"])
ex, fk, clk, closed = rig()
clk.now = clk.now.replace(hour=15, minute=10)
ex.on_ticket_event("opened", ticket())
drain(ex)
check("a ticket at or after 15:10", not fk.places())
ex, fk, clk, closed = rig()
open_and_fill(ex, fk, clk)
ex.on_ticket_event("opened", ticket(tid="NIFTY-20260918-113000"))
drain(ex)
check("a second ticket on an index that still holds a position", len(fk.places(transaction_type="BUY")) == 1)
ex, fk, clk, closed = rig()
ex.on_ticket_event("opened", ticket(expiry="2026-10-06"))
drain(ex)
check("a contract Zerodha does not list: no guess at another", not fk.places()
      and ex.positions[ticket()["trade_id"]]["state"] == "failed")
ex, fk, clk, closed = rig()
ex.entries_today, ex.day = lo.MAX_ENTRIES_PER_DAY, "2026-09-18"
ex.on_ticket_event("opened", ticket())
drain(ex)
check("beyond the daily hard cap", not fk.places())
ex, fk, clk, closed = rig()
ex.on_ticket_event("opened", ticket())
drain(ex)
ex.on_ticket_event("opened", ticket())
drain(ex)
check("the same ticket twice (a re-sent event): one order", len(fk.places(transaction_type="BUY")) == 1)

print("10. A SELL THAT WILL NOT FILL, OR IS REFUSED")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
fk.last["NIFTY2026092225000CE"] = 150.0
ex.on_ticket_event("closed", dict(t, status="CLOSED — cleared manually"))
drain(ex)
sell_id = [o for o in fk.orders_.values() if o["order_type"] == "LIMIT" and o["transaction_type"] == "SELL"][0]["order_id"]
fk.last["NIFTY2026092225000CE"] = 140.0
clk.advance(lo.REPRICE_S)
drain(ex)
check("unfilled after REPRICE_S: repriced under the new price, not a second order",
      ("modify", sell_id, 135.8) in fk.calls and len(fk.places(transaction_type="SELL", order_type="LIMIT")) == 1,
      [c for c in fk.calls if c[0] == "modify"])

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
ex.on_ticket_event("closed", dict(t, status="CLOSED — cleared manually"))
drain(ex)
for i in range(lo.MAX_EXIT_ATTEMPTS + 3):
    live = [o for o in fk.orders_.values() if o["order_type"] == "LIMIT" and o["transaction_type"] == "SELL"
            and o["status"] == "OPEN"]
    for o in live:
        fk.reject(o["order_id"], "RMS rejected")
    clk.advance(lo.REPRICE_S)
    drain(ex)
p = ex.positions[t["trade_id"]]
check("refused every time: stops at MAX_EXIT_ATTEMPTS and says to check Kite",
      len(fk.places(transaction_type="SELL", order_type="LIMIT")) == lo.MAX_EXIT_ATTEMPTS and p["state"] == "attention"
      and any("CHECK KITE" in n["text"] for n in ex.notes), len(fk.places(transaction_type="SELL", order_type="LIMIT")))

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
def down(oid):
    raise NetworkException("Read timed out")
fk.order_history = down
before = len(ex.notes)
for _ in range(30):
    clk.advance(1)
    ex.poll()
check("Zerodha not answering for 30s: said once, not once a second",
      len(ex.notes) - before == 1 and "Read timed out" in ex.notes[0]["text"], len(ex.notes) - before)
clk.advance(60)
ex.poll()
check("...and said again after a minute if it is still down", len(ex.notes) - before == 2)

print("11. SOLD OUTSIDE THE TOOL (IN KITE) - NEVER SELL WHAT IS NOT HELD")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
stop_id = [o for o in fk.orders_.values() if o["order_type"] == "SL"][0]["order_id"]
fk.manual_sold = 130
clk.advance(2)
drain(ex)
check("inside SETTLE_S of the fill, positions are not trusted to shrink the holding",
      ex.positions[t["trade_id"]]["state"] == "open")
clk.advance(lo.VERIFY_S)
drain(ex)
check("after it: all sold in Kite -> the stop order is cancelled and the position closed",
      ("cancel", stop_id) in fk.calls and ex.positions[t["trade_id"]]["state"] == "closed")
ex.on_ticket_event("closed", dict(t, status="CLOSED — T2 hit (full target reached)"))
drain(ex)
check("and the ticket's later close sells nothing", not fk.places(transaction_type="SELL", order_type="LIMIT"))

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
fk.manual_sold = 65
clk.advance(lo.VERIFY_S + 1)
drain(ex)
sl = fk.places(order_type="SL")
check("half sold in Kite: the stop is re-placed for the half still held",
      len(sl) == 2 and sl[1]["quantity"] == 65 and ex._held(ex.positions[t["trade_id"]]) == 65, [s["quantity"] for s in sl])

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
clk.advance(lo.SETTLE_S + 1)
fk.manual_sold = 30
ex.on_ticket_event("closed", dict(t, status="CLOSED — T2 hit (full target reached)"))
drain(ex)
s = fk.places(transaction_type="SELL", order_type="LIMIT")
check("on exit, only what Zerodha shows as held is sold", s and s[0]["quantity"] == 100, s)

print("12. RESTARTS")
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
ex, fk, clk, closed = rig(path=path)
t, _ = open_and_fill(ex, fk, clk)
check("state is on disk", json.load(open(path + ".live.json"))["positions"][t["trade_id"]]["state"] == "open")
ex2 = lo.Executor("me@example.invalid", path, kite=lambda: fk, now=lambda: clk.now, clock=lambda: clk.t, start=False)
check("a new process reads the switch back", ex2.enabled["NIFTY"] is True)
drain(ex2)
check("and sells a position its (now closed) ticket left behind, cancelling the stop",
      len(fk.places(transaction_type="SELL", order_type="LIMIT")) == 1
      and any(c[0] == "cancel" for c in fk.calls), fk.calls[-3:])

path = os.path.join(tempfile.mkdtemp(), "trades.csv")
ex, fk, clk, closed = rig(path=path)
open_and_fill(ex, fk, clk)
clk.now += dt.timedelta(days=1)
ex3 = lo.Executor("me@example.invalid", path, kite=lambda: fk, now=lambda: clk.now, clock=lambda: clk.t, start=False)
n = len(fk.calls)
drain(ex3)
check("a position from an earlier day is not traded - Zerodha squared it off at the close",
      not [c for c in fk.calls[n:] if c[0] in ("place", "cancel")])

ex, fk, clk, closed = rig()
fk.raise_on_place = [NetworkException("Read timed out")]
ex.on_ticket_event("opened", ticket())
drain(ex)
p = ex.positions[ticket()["trade_id"]]
check("no answer from Zerodha on the entry: not assumed failed, checked by its tag", p["state"] == "placing")
fk.n += 1
fk.orders_["2001"] = {"order_id": "2001", "tag": p["tag"], "transaction_type": "BUY", "tradingsymbol": p["tradingsymbol"],
                      "quantity": 130, "order_type": "LIMIT", "status": "OPEN", "filled_quantity": 0,
                      "average_price": 0, "status_message": None, "product": "MIS"}
drain(ex)
check("found: carried on as a normal entry", p["state"] == "entering" and p["entry_order_id"] == "2001")
fk.fill("2001", price=131.0)
drain(ex)
check("and protected when it fills", len(fk.places(order_type="SL")) == 1)

ex, fk, clk, closed = rig()
fk.raise_on_place = [NetworkException("Read timed out")]
ex.on_ticket_event("opened", ticket())
drain(ex)
clk.advance(lo.PLACING_WAIT_S - 1)
drain(ex)
check("not listed yet: still looking", ex.positions[ticket()["trade_id"]]["state"] == "placing")
clk.advance(2)
drain(ex)
check("never listed within PLACING_WAIT_S: taken as never sent, nothing else placed",
      ex.positions[ticket()["trade_id"]]["state"] == "failed" and len(fk.places()) == 1)

ex, fk, clk, closed = rig()
fk.raise_on_place = [NetworkException("Read timed out")]
ex.on_ticket_event("opened", ticket())
drain(ex)
p = ex.positions[ticket()["trade_id"]]
ex.on_ticket_event("closed", dict(ticket(), status="CLOSED — cleared manually"))
fk.orders_["2001"] = {"order_id": "2001", "tag": p["tag"], "transaction_type": "BUY", "tradingsymbol": p["tradingsymbol"],
                      "quantity": 130, "order_type": "LIMIT", "status": "COMPLETE", "filled_quantity": 130,
                      "average_price": 131.0, "status_message": None, "product": "MIS"}
drain(ex)
check("found after its ticket closed: sold, not protected",
      not fk.places(order_type="SL") and len(fk.places(transaction_type="SELL", order_type="LIMIT")) == 1)

path = os.path.join(tempfile.mkdtemp(), "trades.csv")
ex, fk, clk, closed = rig(path=path)
fk.raise_on_place = [NetworkException("Read timed out")]
ex.on_ticket_event("opened", ticket())
drain(ex)
tag = ex.positions[ticket()["trade_id"]]["tag"]
ex4 = lo.Executor("me@example.invalid", path, kite=lambda: fk, now=lambda: clk.now, clock=lambda: clk.t, start=False)
fk.orders_["3001"] = {"order_id": "3001", "tag": tag, "transaction_type": "BUY", "tradingsymbol": "NIFTY2026092225000CE",
                      "quantity": 130, "order_type": "LIMIT", "status": "COMPLETE", "filled_quantity": 130,
                      "average_price": 131.0, "status_message": None, "product": "MIS"}
drain(ex4)
check("a restart while an entry was unanswered: once found, it is sold - its ticket is gone",
      not fk.places(order_type="SL") and len(fk.places(transaction_type="SELL", order_type="LIMIT")) == 1)

print("12b. THE AI DESK'S OWN SWITCH, APART FROM THE RULE TICKETS'")
ex, fk, clk, closed = rig(enabled=())
check("both switches start off", ex.public()["enabled"]["NIFTY"] is False and ex.public()["enabled_ai"]["NIFTY"] is False)
ex.on_ticket_event("opened", ticket(tid="AI-1"), source="ai")
drain(ex)
check("an AI ticket with the AI switch off: no order", not fk.places())
ex.set_enabled("NIFTY", True, "ai")
check("switching AI orders on leaves the rule tickets' switch off",
      ex.enabled_ai["NIFTY"] is True and ex.enabled["NIFTY"] is False)
ex.on_ticket_event("opened", ticket(tid="AI-2"), source="ai")
drain(ex)
buys = fk.places(transaction_type="BUY")
check("now the AI ticket is bought", len(buys) == 1 and buys[0]["tradingsymbol"] == "NIFTY2026092225000CE", buys)
check("the position remembers whose ticket it is", ex.positions["AI-2"]["source"] == "ai"
      and ex.public()["positions"][0]["source"] == "ai")
check("...and the note says so", any("AI ticket" in n["text"] for n in ex.notes))
ex.on_ticket_event("opened", ticket(tid="RULE-1"))
drain(ex)
check("a rule ticket on the same index is still not bought - its own switch is off",
      len(fk.places(transaction_type="BUY")) == 1)
ex.set_enabled("NIFTY", True)
ex.on_ticket_event("opened", ticket(tid="RULE-2"))
drain(ex)
check("with both switches on, the rule ticket and the AI ticket can both be live on one index",
      len(fk.places(transaction_type="BUY")) == 2 and ex.positions["RULE-2"]["source"] == "rule")
ex.on_ticket_event("opened", ticket(tid="AI-3"), source="ai")
drain(ex)
check("but never two live AI positions on the same index",
      len(fk.places(transaction_type="BUY")) == 2 and any("already holds a live AI position" in n["text"] for n in ex.notes))
ex.set_enabled("NIFTY", False, "ai")
ex.on_ticket_event("opened", ticket(tid="AI-4"), source="ai")
drain(ex)
check("switched off again: no new AI order", len(fk.places(transaction_type="BUY")) == 2)
check("both switches survive a restart", lo.Executor("me@example.invalid", ex.path[:-len(".live.json")],
      kite=lambda: fk, now=lambda: clk.now, clock=lambda: clk.t, start=False).enabled["NIFTY"] is True)

print("13. ONE EXECUTOR PER ACCOUNT")
lo._registry.clear()
fk = FakeKite()
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
orig = lo.Executor.__init__
def no_thread(self, *a, **kw):
    kw["start"] = False
    orig(self, *a, **kw)
lo.Executor.__init__ = no_thread
try:
    a = lo.for_account("Me@Example.invalid", path, close_ticket=lambda i, s: None, kite=lambda: fk)
    b = lo.for_account("me@example.invalid", path, close_ticket=lambda i, s: None, kite=lambda: fk)
    check("a rebuilt feed gets the same executor, never a second one on the same positions", a is b)
finally:
    lo.Executor.__init__ = orig
    lo._registry.clear()

print("14. THE TICKET ENGINE TELLS IT")
import tickets
book = tickets.TicketBook(owner="me@example.invalid", market="nse_index")
seen = []
book.listeners.append(lambda kind, trade: seen.append((kind, dict(trade))))
rec = {"index": "NIFTY", "option_type": "CE", "suggested_strike": 25000, "option_chain": {"expiry": EXP},
       "spot": 24990.0, "live_ltp": 130.0, "premium_source": "live", "index_targets": [25040, 25080, 25120],
       "index_stop_loss": 24950, "premium_targets": [156.0, 182.0, 227.0], "premium_stop_loss": 97.5}
nb = book.books["NIFTY"]
book._open(nb, rec)
check("a ticket opening calls the listener with the frozen levels",
      seen and seen[0][0] == "opened" and seen[0][1]["premium_sl"] == 97.5 and seen[0][1]["use_premium"] is True
      and str(seen[0][1]["expiry"]) == EXP)
book.close_ticket("NIFTY", "CLOSED — intraday close at 15:20 (live order)")
check("close_ticket closes it with that status, and the listener hears it",
      seen[-1][0] == "closed" and seen[-1][1]["status"].startswith("CLOSED — intraday close") and nb.trade is None)
book.listeners[:] = [lambda kind, trade: 1 / 0]
book._open(nb, rec)
check("a listener that raises cannot break the ticket engine", nb.trade is not None)

print("15. THE SWITCH ON THE PAGE")
import accounts
import feeds
import user_kite
import web_server

ME = "me@example.invalid"


class LiveFeed:
    def __init__(self, ex):
        self.live = ex


def handler(ex, user=ME, same_origin=True, market="nse_index"):
    feeds.for_user = lambda email, mkt=None, start=True: LiveFeed(ex)
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    h._current_user = lambda: user
    h._same_origin = lambda: same_origin
    return h, out


_saved = (feeds.for_user, user_kite.token_for, accounts.get_user, web_server._state.get("mode"))
try:
    web_server._state["mode"] = "kite"
    ex, fk, clk, closed = rig(enabled=())
    user_kite.token_for = lambda email: "tok"
    accounts.get_user = lambda email: {"always_on": True}

    h, out = handler(ex, same_origin=False)
    h._do_live({"index": "NIFTY", "on": "1"})
    check("a cross-site request cannot switch real orders on (403)", out["code"] == 403 and not ex.enabled["NIFTY"])
    h, out = handler(ex, user=None)
    h._do_live({"index": "NIFTY", "on": "1"})
    check("signed out (401)", out["code"] == 401 and not ex.enabled["NIFTY"])
    h, out = handler(ex, market="crypto")
    h._do_live({"index": "NIFTY", "on": "1"})
    check("not from the Bitcoin market (400)", out["code"] == 400 and not ex.enabled["NIFTY"])
    h, out = handler(ex)
    h._do_live({"index": "BTC", "on": "1"})
    check("not for an index outside the three (400)", out["code"] == 400)
    user_kite.token_for = lambda email: None
    h, out = handler(ex)
    h._do_live({"index": "NIFTY", "on": "1"})
    check("not without today's Zerodha login", out["code"] == 400 and "Connect Zerodha" in json.loads(out["body"])["message"]
          and not ex.enabled["NIFTY"])
    user_kite.token_for = lambda email: "tok"
    accounts.get_user = lambda email: {"always_on": False}
    h, out = handler(ex)
    h._do_live({"index": "NIFTY", "on": "1"})
    check("not unless the tool runs all session - closing the page would stop target exits",
          out["code"] == 400 and "runs all session" in json.loads(out["body"])["message"] and not ex.enabled["NIFTY"])
    accounts.get_user = lambda email: {"always_on": True}
    web_server._state["mode"] = "free"
    h, out = handler(ex)
    h._do_live({"index": "NIFTY", "on": "1"})
    check("not in free data mode", out["code"] == 400 and not ex.enabled["NIFTY"])
    web_server._state["mode"] = "kite"
    h, out = handler(ex)
    h._do_live({"index": "NIFTY", "on": "1"})
    d = json.loads(out["body"])
    check("all conditions met: switched on, and the page gets the new state", d["ok"] and ex.enabled["NIFTY"]
          and d["live"]["enabled"]["NIFTY"] is True)
    check("switching one index on leaves the others off", not ex.enabled["BANKNIFTY"] and not ex.enabled["SENSEX"])
    user_kite.token_for = lambda email: None
    accounts.get_user = lambda email: {"always_on": False}
    h, out = handler(ex)
    h._do_live({"index": "NIFTY", "on": "0"})
    check("switching OFF is always allowed, whatever else is missing", json.loads(out["body"])["ok"] and not ex.enabled["NIFTY"])
    user_kite.token_for = lambda email: "tok"
    accounts.get_user = lambda email: {"always_on": True}
    h, out = handler(ex)
    h._do_live({"index": "NIFTY", "on": "1", "source": "ai"})
    d = json.loads(out["body"])
    check("the AI desk's own live switch goes through the same door",
          d["ok"] and ex.enabled_ai["NIFTY"] and not ex.enabled["NIFTY"] and "AI trades on NIFTY" in d["message"], d["message"])
    h, out = handler(ex)
    h._do_live({"index": "NIFTY", "on": "1", "source": "everything"})
    check("an unknown kind of ticket is refused", out["code"] == 400)
    user_kite.token_for = lambda email: None
    h, out = handler(ex)
    h._do_live({"index": "NIFTY", "on": "1", "source": "ai"})
    check("AI live orders need today's Zerodha login too", out["code"] == 400)
finally:
    feeds.for_user, user_kite.token_for, accounts.get_user = _saved[:3]
    web_server._state["mode"] = _saved[3]

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("the page has the per-index button and the status line", 'id="tlive"' in SRC and 'id="tlivestat"' in SRC)
check("turning it on asks first, naming real money and the static IP",
      "Place REAL orders on" in SRC and "static IP" in SRC)
check("Clear ticket warns that it sells a real position when one is held", "AND SELL YOUR REAL POSITION" in SRC)
check("status text is set as text, never HTML", 'st.textContent = lines' in SRC)
FSRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "feeds.py")).read()
check("only the Indian-indices feed gets an executor, and it listens to that feed's tickets",
      'self.market == "nse_index"' in FSRC and "self.tickets.listeners.append(self.live.on_ticket_event)" in FSRC)

print("LIVE ORDERS TEST PASSED" if not fails else f"LIVE ORDERS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
