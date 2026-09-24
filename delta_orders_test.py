#!/usr/bin/env python3
"""Live Delta Exchange India orders that follow a Bitcoin ticket - against a
fake Delta only. What matters most: the right contract and count, the stop
watched by the tool (Delta holds none), never selling more than is held, the
sell before settlement, the wallet check, and what a restart does.
Nothing here can reach a real account."""
import datetime as dt
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import delta_orders as do
import delta_provider

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SYM = "P-BTC-80000-250926"
delta_provider.DeltaDataProvider.option_instrument = lambda self, k, strike, kind, expiry=None: (
    None if int(float(strike)) == 12345 else f"{'C' if kind == 'CE' else 'P'}-BTC-{int(float(strike))}-250926")


class FakeDelta:
    """Just enough of Delta: orders fill only when the test says so."""

    def __init__(self):
        self.orders_ = {}
        self.calls = []
        self.n = 0
        self.mark = 1500.0
        self.cash = 1_000_000.0
        self.settle = "2026-09-25T12:00:00Z"
        self.manual_sold = 0
        self.raise_on_place = []
        # How Delta answers a position call. "dict" is the REAL shape: one bare object with
        # no product_id in it (with nothing held, {"size": 0, "entry_price": null} - seen live
        # on 24 Sep 2026). The others are the ways a reply can be unreadable or wrong.
        self.shape = "dict"
        self.glitch = 0                 # this many replies show zero contracts although they are held
        self.position_error = False

    def product(self, symbol):
        self.calls.append(("product", symbol))
        return {"id": 777, "tick_size": "0.1", "contract_value": "0.001", "settling_asset": {"symbol": "USD"},
                "settlement_time": self.settle, "symbol": symbol}

    def ticker(self, symbol):
        return {"mark_price": str(self.mark), "close": self.mark - 1}

    def balances(self):
        self.calls.append(("balances",))
        return [{"asset_symbol": "USD", "available_balance": str(self.cash), "balance": str(self.cash)}]

    def place_order(self, **kw):
        self.calls.append(("place", kw))
        if self.raise_on_place:
            raise self.raise_on_place.pop(0)
        self.n += 1
        oid = 5000 + self.n
        self.orders_[oid] = dict(kw, id=oid, state="open", unfilled_size=kw["size"], average_fill_price=None)
        return {"id": oid, "state": "open"}

    def cancel_order(self, order_id, product_id):
        self.calls.append(("cancel", int(order_id)))
        o = self.orders_[int(order_id)]
        if o["state"] in ("closed", "cancelled"):
            raise Exception("order already done")
        o["state"] = "cancelled"

    def edit_order(self, order_id, product_id, **kw):
        self.orders_[int(order_id)].update(kw)

    def order(self, order_id):
        o = self.orders_[int(order_id)]
        return {k: o.get(k) for k in ("id", "state", "size", "unfilled_size", "average_fill_price", "client_order_id", "side")}

    def open_orders(self, product_id):
        return [self.order(i) for i, o in self.orders_.items() if o["state"] in ("open", "pending")]

    def position(self, product_id):
        self.calls.append(("position",))
        if self.position_error:
            raise Exception("positions unavailable")
        net = 0
        for o in self.orders_.values():
            filled = o["size"] - o["unfilled_size"]
            net += filled if o["side"] == "buy" else -filled
        net = max(0, net - self.manual_sold)
        if self.glitch > 0:
            self.glitch -= 1
            net = 0
        ep = "1500.0" if net else None
        return {"dict": lambda: {"size": net, "entry_price": ep},
                "dict_pid": lambda: {"product_id": 777, "product_symbol": SYM, "size": net, "entry_price": ep},
                "list": lambda: [{"product_id": 777, "size": net}],
                "other_product": lambda: [{"product_id": 999, "size": net}],
                "empty_list": lambda: [], "none": lambda: None, "no_size": lambda: {"entry_price": None}}[self.shape]()

    # -- the market, driven by the test
    def fill(self, oid, qty=None, price=1500.0):
        o = self.orders_[int(oid)]
        done = o["size"] if qty is None else qty
        o["unfilled_size"] = o["size"] - done
        o["average_fill_price"] = price
        o["state"] = "closed" if o["unfilled_size"] == 0 else "open"

    def places(self, **match):
        return [kw for c in self.calls if c[0] == "place" for kw in [c[1]] if all(kw.get(k) == v for k, v in match.items())]


class Clock:
    def __init__(self):
        self.now = dt.datetime(2026, 9, 20, 11, 0, tzinfo=IST)
        self.t = self.now.timestamp()             # the epoch and the wall clock agree
    def advance(self, s):
        self.t += s
        self.now += dt.timedelta(seconds=s)


def rig(enabled=("BTC",), ai=(), keys=True):
    fk, clk, closed = FakeDelta(), Clock(), []
    path = os.path.join(tempfile.mkdtemp(), "trades.csv")
    def make():
        if not keys:
            raise RuntimeError("no Delta Exchange keys on this account")
        return fk
    ex = do.Executor("me@example.invalid", path, client=make, close_ticket=lambda i, s: closed.append((i, s)),
                     now=lambda: clk.now, clock=lambda: clk.t, mark=lambda sym: fk.mark, start=False)
    # the settle clock: 2026-09-25 12:00 UTC is far ahead of the fake clock unless a test moves it
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


def ticket(tid=None, strike=80000, opt="PE", lots=10, entry=1500.0, sl=1200.0, target=1900.0, use_premium=True):
    return {"trade_id": tid or "BTC-20260920-110000", "index": "BTC", "strike": strike, "option_type": opt,
            "expiry": "2026-09-25", "lots": lots, "use_premium": use_premium, "entry_ltp": entry,
            "premium_sl": sl, "premium_targets": [target] * 3, "status": "OPEN"}


def open_and_fill(ex, fk, clk, t=None, price=1500.0):
    t = t or ticket()
    ex.on_ticket_event("opened", t)
    drain(ex)
    buy = [o for o in fk.orders_.values() if o["side"] == "buy"][-1]
    fk.fill(buy["id"], price=price)
    drain(ex)
    return t, buy


print("1. NOTHING HAPPENS UNLESS BITCOIN IS SWITCHED ON, AND KEYS EXIST")
ex, fk, clk, closed = rig(enabled=())
ex.on_ticket_event("opened", ticket())
check("the callback only queues", ex.q.qsize() == 1 and not fk.calls)
drain(ex)
check("switched off: no order", not fk.places())
try:
    ex.set_enabled("NIFTY", True); check("only Bitcoin", False)
except ValueError:
    check("only Bitcoin", True)
ex, fk, clk, closed = rig(keys=False)
ex.on_ticket_event("opened", ticket())
drain(ex)
pos = ex.positions[ticket()["trade_id"]]
check("no Delta keys: refused, named, the ticket stays on paper", pos["state"] == "failed" and not fk.places()
      and "Delta Exchange keys" in ex.notes[0]["text"] and "paper ticket" in ex.notes[0]["text"], ex.notes[0]["text"])

print("2. THE ENTRY: DELTA'S CONTRACT, A COUNT OF CONTRACTS, A LIMIT")
ex, fk, clk, closed = rig()
fk.mark = 1510.0
ex.on_ticket_event("opened", ticket())
drain(ex)
b = fk.places(side="buy")
check("one BUY for the exact contract, lots as contracts, limit 2% over the mark, tagged",
      len(b) == 1 and b[0]["product_id"] == 777 and b[0]["size"] == 10 and b[0]["order_type"] == "limit_order"
      and float(b[0]["limit_price"]) == 1540.2 and b[0]["client_order_id"].startswith("TPBTC"), b)
pos = ex.positions[ticket()["trade_id"]]
check("the product was looked up once, the position knows its symbol", pos["symbol"] == SYM and pos["product_id"] == 777
      and sum(1 for c in fk.calls if c[0] == "product") == 1)
check("the wallet was read before the order", any(c[0] == "balances" for c in fk.calls))
ex.on_ticket_event("opened", ticket(tid="BTC-2"))
drain(ex)
check("a second ticket while one is held gets no order", len(fk.places(side="buy")) == 1)

print("3. THE FILL: THE STOP IS THE TOOL'S TO WATCH - NOTHING RESTS AT DELTA")
ex, fk, clk, closed = rig()
t, buy = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
check("bought: open, at Delta's average, and no second order of any kind was placed",
      pos["state"] == "open" and pos["avg_price"] == 1505.0 and len(fk.places()) == 1
      and "watched by this tool" in " ".join(n["text"] for n in ex.notes))
fk.mark = 1400.0
drain(ex)
check("the mark above the stop: held", pos["state"] == "open" and len(fk.places()) == 1)
fk.mark = 1199.0
drain(ex)
s = fk.places(side="sell")
check("the mark at the stop: a reduce-only limit sell 3% under the mark, and the ticket is closed as a stop",
      len(s) == 1 and s[0]["size"] == 10 and s[0]["reduce_only"] is True and float(s[0]["limit_price"]) == 1163.0
      and closed and "stop-loss hit" in closed[-1][1], (s, closed))
ex._sell_rest(pos)
check("a sell already working: _sell_rest itself sends nothing more", len(fk.places(side="sell")) == 1)
fk.fill([o for o in fk.orders_.values() if o["side"] == "sell"][-1]["id"], price=1165.0)
drain(ex); drain(ex); drain(ex)
check("sold: closed at Delta's price", pos["state"] == "closed" and pos["exit_price"] == 1165.0)
rows = do.read_fills(ex.log_path)
check("the real fill is written once: contracts, prices, gross in dollars (x 0.001 per contract)",
      len(rows) == 1 and rows[0]["qty"] == 10 and rows[0]["entry_avg"] == 1505.0 and rows[0]["exit_avg"] == 1165.0
      and rows[0]["gross_pnl"] == round((1165.0 - 1505.0) * 10 * 0.001, 4), rows)

print("4. THE TICKET CLOSES (TARGET, AI EXIT, CLEARED): SELL WHAT IS HELD - ONCE")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
fk.mark = 1900.0
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
check("one sell, however many times the close is heard", len(fk.places(side="sell")) == 1)
sell = [o for o in fk.orders_.values() if o["side"] == "sell"][-1]
clk.advance(do.REPRICE_S + 1)
fk.mark = 1880.0
drain(ex)
check("not filled in 5s: cancelled and re-sent lower - never two sells working at once",
      ("cancel", sell["id"]) in fk.calls and len(fk.places(side="sell")) == 2
      and sum(1 for o in fk.orders_.values() if o["side"] == "sell" and o["state"] == "open") == 1)
for _ in range(3):
    clk.advance(do.REPRICE_S + 1)
    drain(ex)
last = fk.places(side="sell")[-1]
check(f"after {do.LIMIT_ATTEMPTS} limits, a reduce-only MARKET sell finishes it",
      last["order_type"] == "market_order" and last["reduce_only"] is True and last["size"] == 10, last)
fk.fill([o for o in fk.orders_.values() if o["side"] == "sell"][-1]["id"], price=1870.0)
drain(ex); drain(ex)
check("closed, with the sold price", ex.positions[t["trade_id"]]["state"] == "closed" and ex.positions[t["trade_id"]]["exit_price"] == 1870.0)
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
fk.raise_on_place.append(Exception("insufficient_margin"))
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
pos = ex.positions[t["trade_id"]]
check("a refused sell leaves the position exiting with no order working, to retry after REPRICE_S",
      pos["state"] == "exiting" and pos["exit_order_id"] is None and pos["exit_attempts"] == 1)
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
check("hearing the close again does not retry early: an exit under way is never started twice",
      pos["exit_attempts"] == 1 and len(fk.places(side="sell")) == 1)
clk.advance(do.REPRICE_S + 1)
drain(ex)
check("...the retry comes with the reprice clock", pos["exit_attempts"] == 2 and len(fk.places(side="sell")) == 2)

print("5. THE WALLET")
ex, fk, clk, closed = rig()
fk.cash = 10.0                                   # 10 x 1530 x 0.001 = 15.30 (+2% fees)
ex.on_ticket_event("opened", ticket())
drain(ex)
pos = ex.positions[ticket()["trade_id"]]
check("short of dollars: no order, the shortfall named, the ticket stays on paper",
      not fk.places() and pos["state"] == "failed" and "short by USD" in ex.notes[0]["text"], ex.notes[0]["text"])
ex, fk, clk, closed = rig()
r = ex.funds_check(100.0)
check("funds_check for the AI desk: yes/no and the shortfall, never the balance", r == {"enough": True, "shortfall": None}
      and "1000000" not in json.dumps(ex.funds_check(2_000_000.0)) and ex.funds_check(2_000_000.0)["enough"] is False)

print("6. EXPIRY: OUT BEFORE SETTLEMENT, NOTHING NEW CLOSE TO IT")
ex, fk, clk, closed = rig()
fk.settle = "2026-09-20T06:20:00Z"                # 11:50 IST - 50 minutes after the fake clock's 11:00
ex.on_ticket_event("opened", ticket())
drain(ex)
check("within an hour of settlement: no entry", ex.positions[ticket()["trade_id"]]["state"] == "failed"
      and "settles in" in ex.notes[0]["text"], ex.notes[0]["text"])
ex, fk, clk, closed = rig()
fk.settle = "2026-09-20T07:00:00Z"                # 12:30 IST - 90 minutes ahead
t, _ = open_and_fill(ex, fk, clk)
clk.advance(61 * 60)                             # 12:01 IST: 29 minutes to settlement
drain(ex)
check("thirty minutes before settlement: sold, ticket and all", len(fk.places(side="sell")) == 1 and closed
      and "settles" in closed[-1][1], closed)

print("7. SOLD OUTSIDE THE TOOL - NEVER SELL WHAT IS NOT HELD")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
fk.manual_sold = 10
clk.advance(do.VERIFY_S + 1)
drain(ex)
check("one reading of zero is not believed: still open, and it says it is reading again",
      ex.positions[t["trade_id"]]["state"] == "open" and "reading it again" in ex.notes[0]["text"], ex.notes[0]["text"])
clk.advance(do.VERIFY_S + 1)
drain(ex)
check("all sold on Delta's own screen (two readings): closed here, nothing sent", ex.positions[t["trade_id"]]["state"] == "closed"
      and not fk.places(side="sell"))
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
clk.advance(do.SETTLE_S + 1)
fk.manual_sold = 4
ex.on_ticket_event("closed", dict(t, status="CLOSED — AI exit"))
drain(ex)
check("on exit, only what Delta shows as held is sold", fk.places(side="sell")[0]["size"] == 6)

print("7b. 24 SEP 2026: A REAL POSITION WAS WRITTEN OFF, AND ITS STOP SENT NOTHING")
R = do.position_size
cases = [
    ("the real reply for a product with nothing held", {"size": 0, "entry_price": None}, 0),
    ("the real reply for a held position - a bare object, no product_id", {"size": 25, "entry_price": "730.0"}, 25),
    ("the size sent as text", {"size": "25"}, 25), ("a whole number sent as a float", {"size": 12.0}, 12),
    ("a list naming this product", [{"product_id": 777, "size": 10}], 10),
    ("the product id sent as text", {"product_id": "777", "size": 3}, 3),
    ("a list naming ANOTHER product", [{"product_id": 999, "size": 10}], None),
    ("two products: only this one counts", [{"product_id": 999, "size": 10}, {"product_id": 777, "size": 4}], 4),
    ("a row for another symbol", {"product_symbol": "OTHER", "size": 3}, None),
    ("rows that name no product are this product's", [{"size": 10}, {"size": 5}], 15),
    ("no reply at all", None, None), ("an empty list", [], None), ("an empty object", {}, None),
    ("text", "oops", None), ("a size of null", {"size": None}, None), ("a size that is not a number", {"size": "abc"}, None),
    ("a negative size (a short - impossible after buying)", {"size": -25}, None), ("no size in the reply", {"entry_price": None}, None),
]
for name, reply, want in cases:
    check(f"position_size: {name}", R(reply, 777, SYM) == want, (reply, R(reply, 777, SYM)))

# --- the exact failure: the real reply shape, a held position, its stop ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
pos = ex.positions[t["trade_id"]]
for _ in range(4):
    clk.advance(do.VERIFY_S + 1)
    drain(ex)
check("held, and Delta answers in its real shape: still open after four verifications (it was written off after ONE)",
      pos["state"] == "open" and not pos.get("outside_sold") and not pos.get("outside_unconfirmed"), pos["state"])
fk.mark = 1150.0
drain(ex)
s = fk.places(side="sell")
check("...so when the stop is reached the sell IS sent: all of it, reduce-only, and the ticket closes as a stop",
      len(s) == 1 and s[0]["size"] == 10 and s[0]["reduce_only"] is True and closed and "stop-loss hit" in closed[-1][1], s)

# --- the same for a TARGET (the user asked for both) ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
for _ in range(3):
    clk.advance(do.VERIFY_S + 1)
    drain(ex)
ex.on_ticket_event("closed", dict(t, status="CLOSED — T2 hit (full target reached)"))
drain(ex)
s = fk.places(side="sell")
check("a target close sells too: all 10, reduce-only", len(s) == 1 and s[0]["size"] == 10 and s[0]["reduce_only"] is True, s)
fk.fill(next(o["id"] for o in fk.orders_.values() if o["side"] == "sell"), price=1900.0)
drain(ex)
check("...and the position is closed at the fill price", ex.positions[t["trade_id"]]["state"] == "closed"
      and ex.positions[t["trade_id"]]["exit_price"] == 1900.0)

# --- every other shape Delta's reply might take: the position is never lost ---
for shape in ("dict_pid", "list", "other_product", "empty_list", "none", "no_size"):
    ex, fk, clk, closed = rig()
    fk.shape = shape
    t, _ = open_and_fill(ex, fk, clk)
    for _ in range(4):
        clk.advance(do.VERIFY_S + 1)
        drain(ex)
    pos = ex.positions[t["trade_id"]]
    ok_state = pos["state"] == "open"
    ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
    drain(ex)
    s = fk.places(side="sell")
    check(f"reply shape '{shape}': never written off, and the stop still sells everything held",
          ok_state and len(s) == 1 and s[0]["size"] == 10, (shape, pos["state"], s))
ex, fk, clk, closed = rig()
fk.position_error = True
t, _ = open_and_fill(ex, fk, clk)
for _ in range(4):
    clk.advance(do.VERIFY_S + 1)
    drain(ex)
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
check("positions unavailable altogether: not written off, and the stop still sells", ex.positions[t["trade_id"]]["state"] == "exiting"
      and len(fk.places(side="sell")) == 1)

# --- a wrong reading does not stand ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
pos = ex.positions[t["trade_id"]]
clk.advance(do.VERIFY_S + 1)
fk.glitch = 1
drain(ex)
check("one wrong reading of zero: not believed", pos["state"] == "open" and pos.get("flat_reads") == 1)
clk.advance(do.VERIFY_S + 1)
drain(ex)
check("the next reading is right: the count starts again", pos["state"] == "open" and pos.get("flat_reads") == 0)

# --- two wrong readings DO write it off - and the close then looks again and sells ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
pos = ex.positions[t["trade_id"]]
fk.glitch = 2
for _ in range(2):
    clk.advance(do.VERIFY_S + 1)
    drain(ex)
check("two wrong readings in a row: written off, but flagged as unconfirmed", pos["state"] == "closed" and pos.get("outside_unconfirmed") is True)
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
s = fk.places(side="sell")
check("the ticket closing looks again, finds it held, and SELLS it - the 24 Sep failure cannot end with nothing sent",
      len(s) == 1 and s[0]["size"] == 10 and s[0]["reduce_only"] is True, s)
check("...and says so", any("still" in n["text"] and "selling it" in n["text"] for n in ex.notes), [n["text"] for n in ex.notes[:3]])

# --- really sold elsewhere: confirmed, nothing sent ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
fk.manual_sold = 10
for _ in range(2):
    clk.advance(do.VERIFY_S + 1)
    drain(ex)
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
check("sold on Delta's own screen: on the close Delta confirms none held - stays closed, nothing sent",
      ex.positions[t["trade_id"]]["state"] == "closed" and not fk.places(side="sell")
      and ex.positions[t["trade_id"]].get("outside_unconfirmed") is False)

# --- written off while Delta cannot be read: the close still tries ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
fk.glitch = 2
for _ in range(2):
    clk.advance(do.VERIFY_S + 1)
    drain(ex)
fk.position_error = True
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit (full target reached)"))
drain(ex)
check("written off, then Delta cannot be read when the target closes: the sell is still tried", len(fk.places(side="sell")) == 1
      and fk.places(side="sell")[0]["size"] == 10)

# --- a zero reading at the moment of the exit does not stop the sell ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
clk.advance(do.SETTLE_S + 1)
fk.glitch = 1
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
s = fk.places(side="sell")
check("a wrong zero at the very moment of the exit: the sell is sent anyway (it used to end 'nothing left to sell')",
      len(s) == 1 and s[0]["size"] == 10 and ex.positions[t["trade_id"]]["state"] == "exiting", (s, ex.positions[t["trade_id"]]["state"]))

# --- really flat and Delta refuses: the position is taken as gone, but only for the right reason ---
class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
clk.advance(do.SETTLE_S + 1)
fk.manual_sold = 10
fk.raise_on_place.extend([Refused("reduce_only_would_increase"), Refused("reduce_only_would_increase")])
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
check("flat, and Delta refuses the sell ONCE: not believed yet - still trying",
      ex.positions[t["trade_id"]]["state"] == "exiting" and "already closed" not in " ".join(n["text"] for n in ex.notes))
clk.advance(do.REPRICE_S + 1)
drain(ex)
check("flat, and Delta refuses the sell a SECOND time: taken as already closed, at once (not a poll later)",
      ex.positions[t["trade_id"]]["state"] == "closed" and ex._held(ex.positions[t["trade_id"]]) == 0
      and "already closed" in " ".join(n["text"] for n in ex.notes), ex.positions[t["trade_id"]]["state"])
for code in ("ip_not_whitelisted_for_api_key", "unauthorized", "expired_signature", "http 503"):
    ex, fk, clk, closed = rig()
    t, _ = open_and_fill(ex, fk, clk)
    clk.advance(do.SETTLE_S + 1)
    fk.manual_sold = 10
    fk.raise_on_place.extend([Refused(code)] * 4)
    ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
    drain(ex)
    for _ in range(3):
        clk.advance(do.REPRICE_S + 1)
        drain(ex)
    check(f"a refusal about '{code}' says nothing about the position: it is never taken as gone",
          ex.positions[t["trade_id"]]["state"] == "exiting", ex.positions[t["trade_id"]]["state"])
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
clk.advance(do.SETTLE_S + 1)
fk.raise_on_place.extend([Refused("insufficient_margin")] * 5)
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
for _ in range(3):
    clk.advance(do.REPRICE_S + 1)
    drain(ex)
check("refused while Delta still shows the contracts held: never taken as gone, it keeps trying",
      ex.positions[t["trade_id"]]["state"] == "exiting")

# --- flat, and the sell is accepted but can never fill ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
clk.advance(do.SETTLE_S + 1)
fk.manual_sold = 10
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
for _ in range(4):
    clk.advance(do.REPRICE_S + 1)
    drain(ex)
check("flat, and a sell that rests and never fills: after two tries it is taken as gone, not retried for ever",
      ex.positions[t["trade_id"]]["state"] == "closed" and len(fk.places(side="sell")) <= 3, (ex.positions[t["trade_id"]]["state"], len(fk.places(side="sell"))))

# --- flat, and Delta cancels each sell without filling it ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
clk.advance(do.SETTLE_S + 1)
fk.manual_sold = 10
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
first = [o for o in fk.orders_.values() if o["side"] == "sell"][-1]
first["state"] = "cancelled"                                   # Delta ends it: nothing to reduce
drain(ex)
check("flat, one sell cancelled unfilled: not believed yet - a second sell is tried",
      ex.positions[t["trade_id"]]["state"] == "exiting" and len(fk.places(side="sell")) == 2)
second = [o for o in fk.orders_.values() if o["side"] == "sell"][-1]
second["state"] = "cancelled"
drain(ex)
check("flat, a second sell cancelled unfilled: taken as gone, at once",
      ex.positions[t["trade_id"]]["state"] == "closed" and len(fk.places(side="sell")) == 2, (ex.positions[t["trade_id"]]["state"], len(fk.places(side="sell"))))

# --- a restart revisits a written-off position, but leaves an old record alone ---
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
fk.glitch = 2
for _ in range(2):
    clk.advance(do.VERIFY_S + 1)
    drain(ex)
ex2 = do.Executor("me@example.invalid", ex.log_path, client=lambda: fk, close_ticket=lambda i, s: None,
                  now=lambda: clk.now, clock=lambda: clk.t, mark=lambda sym: fk.mark, start=False)
check("a restart queues a written-off, unconfirmed position to be looked at again", ex2.q.qsize() == 1, ex2.q.qsize())
drain(ex2)
check("a restart looks again at a position written off as unconfirmed, and sells it if held",
      len(fk.places(side="sell")) == 1, len(fk.places(side="sell")))
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
ex.positions[t["trade_id"]].update(state="closed", outside_sold=10, exit_reason="it was closed outside the tool")   # a record from before this fix
ex._save()
ex3 = do.Executor("me@example.invalid", ex.log_path, client=lambda: fk, close_ticket=lambda i, s: None,
                  now=lambda: clk.now, clock=lambda: clk.t, mark=lambda sym: fk.mark, start=False)
check("an old written-off record (no flag) is not even queued at start", ex3.q.qsize() == 0, ex3.q.qsize())
drain(ex3)
check("an old written-off record (no flag) is left alone: deploying this never sends an order for it", not fk.places(side="sell"))

print("8. RESTARTS")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
ex2 = do.Executor("me@example.invalid", ex.log_path, client=lambda: fk, close_ticket=lambda i, s: None,
                  now=lambda: clk.now, clock=lambda: clk.t, mark=lambda sym: fk.mark, start=False)
drain(ex2)
check("a position found open on start is sold (its ticket died with the old process)",
      len(fk.places(side="sell")) == 1 and "restarted" in (fk.places(side="sell") and ex2.positions[t["trade_id"]]["exit_reason"]))
ex, fk, clk, closed = rig()
class Timeout(Exception):
    pass
fk.raise_on_place.append(Timeout("read timed out"))
ex.on_ticket_event("opened", ticket())
drain(ex)
pos = ex.positions[ticket()["trade_id"]]
check("an unanswered entry is looked for by its tag, not taken as failed", pos["state"] == "placing")
clk.advance(do.PLACING_WAIT_S + 1)
drain(ex)
check("...and, absent at Delta, failed without a phantom position", pos["state"] == "failed")

print("9. WHAT THE PAGE AND THE AI DESK SEE")
ex, fk, clk, closed = rig(ai=("BTC",))
p = ex.public()
check("the same shape as the Zerodha executor, and it says the stop is not at the venue",
      set(p) >= {"enabled", "enabled_ai", "positions", "notes", "entries_today", "max_entries"}
      and p["venue"] == "Delta Exchange India" and p["stop_at_venue"] is False and p["enabled_ai"]["BTC"] is True)
check("switching on says the stop is the tool's", "watched by this tool" in ex.notes[0]["text"])
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "delta_orders.py")).read()
check("no stop order is ever sent to Delta", "stop_order_type" not in SRC and "stop_price" not in SRC)

print("10. THE SWITCH ON THE PAGE, FOR BITCOIN")
import accounts
import feeds
import user_delta
import web_server
ex, fk, clk, closed = rig(enabled=())


def handler(same_origin=True):
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_user = lambda: "me@example.invalid"
    h._current_market = lambda: "crypto"
    h._same_origin = lambda: same_origin
    return h, out


class FeedStub:
    live = ex
saved = (feeds.for_user, user_delta.keys_for, accounts.get_user, web_server._state["mode"])
try:
    feeds.for_user = lambda user, market=None, start=True: FeedStub()
    user_delta.keys_for = lambda email: None
    accounts.get_user = lambda email: {"always_on": True}
    web_server._state["mode"] = "kite"
    h, out = handler()
    h._do_live({"index": "BTC", "on": "1"})
    check("no Delta keys: refused, and told where to add them", out["code"] == 400 and "Delta Exchange keys" in out["body"]
          and not ex.enabled["BTC"])
    user_delta.keys_for = lambda email: ("k", "s")
    h, out = handler()
    h._do_live({"index": "NIFTY", "on": "1"})
    check("only Bitcoin in the crypto market", out["code"] == 400)
    h, out = handler()
    h._do_live({"index": "BTC", "on": "1"})
    d = json.loads(out["body"])
    check("keys and always-on: switched on, and the page gets the executor's state, which says the stop is not at the venue",
          d["ok"] and ex.enabled["BTC"] and d["live"]["stop_at_venue"] is False and d["live"]["venue"] == "Delta Exchange India")
    h, out = handler()
    h._do_live({"index": "BTC", "on": "1", "source": "ai"})
    check("the AI desk's own switch for Bitcoin", json.loads(out["body"])["ok"] and ex.enabled_ai["BTC"])
    accounts.get_user = lambda email: {"always_on": False}
    h, out = handler()
    h._do_live({"index": "BTC", "on": "0"})
    check("off is always allowed", json.loads(out["body"])["ok"] and not ex.enabled["BTC"])
finally:
    feeds.for_user, user_delta.keys_for, accounts.get_user, web_server._state["mode"] = saved
WS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
FS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "feeds.py")).read()
check("the crypto feed gets the Delta executor, with the socket's mark for the stop watch",
      "delta_orders.for_account(" in FS and "mark=self._crypto_mark" in FS)
check("both confirm dialogs tell a Bitcoin user that Delta holds no stop order and the tool watches the mark",
      WS.count("DELTA HOLDS NO STOP ORDER ON AN OPTION") == 2 and "Place REAL orders on Bitcoin at Delta Exchange India" in WS
      and "Place REAL Delta Exchange orders for the AI desk's Bitcoin trades" in WS)

check("the live-status line for Bitcoin says the stop is the tool's and not at Delta - on the Signal card and the AI tab - "
      "while Zerodha's wording is kept for the indices",
      "function liveStateText(p, stopWatchedByTool)" in WS and "liveStateText(p, L.stop_at_venue === false)" in WS
      and "liveStateText(lp, d.live_stop_at_venue === false)" in WS
      and "NOT held at Delta, so it only works while the server is running" in WS
      and "stop-loss order at Zerodha, trigger ${p.stop_trigger}" in WS
      and WS.count("stop-loss order at Zerodha, trigger") == 1)
check("the AI payload carries whether the stop is at the venue", 'payload["live_stop_at_venue"] = pub.get("stop_at_venue", True)' in WS)

print("11. THE LEVEL THIS TOOL WATCHES TRAILS AS THE TICKET DOES")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)           # sl=1200.0
pos = ex.positions[t["trade_id"]]
check("starts at the ticket's entry-time stop", pos["stop_trigger"] == 1200.0)
ex.on_ticket_event("trailed", dict(t, premium_sl=1350.0))  # T1 crossed
drain(ex)
check("no order to move - Delta holds none - just the level this tool compares the mark against",
      pos["stop_trigger"] == 1350.0 and not [c for c in fk.calls if c[0] in ("modify", "sell")])
fk.mark = 1300.0
drain(ex)
check("the mark falling below the TRAILED level closes it, well above the original 1200 stop",
      pos["state"] != "open" and closed and "stop-loss hit" in closed[-1][1], closed)

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
ex.on_ticket_event("trailed", dict(t, premium_sl=1350.0))
drain(ex)
ex.on_ticket_event("trailed", dict(t, premium_sl=1300.0))  # a stale/lower reading never moves it backward
drain(ex)
check("the level can only ever improve, never fall back", pos["stop_trigger"] == 1350.0)

ex2, fk2, clk2, closed2 = rig()
ex2.on_ticket_event("opened", ticket(tid="BTC-entering"))
drain(ex2)                                                 # bought, but the fill hasn't been sent yet
ex2.on_ticket_event("trailed", dict(ticket(tid="BTC-entering"), premium_sl=1350.0))
drain(ex2)
check("a trail before the position is held is a no-op, not an error - the level stays at the entry-time stop",
      ex2.positions["BTC-entering"]["state"] != "open"
      and ex2.positions["BTC-entering"]["stop_trigger"] == 1200.0)

ex5, fk5, clk5, closed5 = rig()
t5, _ = open_and_fill(ex5, fk5, clk5, price=1505.0)
pos5 = ex5.positions[t5["trade_id"]]
ex5.on_ticket_event("closed", dict(t5, status="CLOSED — AI exit"))
drain(ex5)
check("mid-exit: no longer 'open'", pos5["state"] != "open", pos5["state"])
ex5.on_ticket_event("trailed", dict(t5, premium_sl=1350.0))
drain(ex5)
check("...so a trail arriving mid-exit is dropped, not applied to a position already on its way out",
      pos5["stop_trigger"] == 1200.0, pos5["stop_trigger"])

ex3, fk3, clk3, closed3 = rig(enabled=())                 # Bitcoin switched off: never entered at all
ex3.on_ticket_event("trailed", dict(ticket(), premium_sl=1350.0))
drain(ex3)
check("a trail for a ticket this executor never took is dropped, not mistaken for a close",
      not fk3.calls and not closed3)

print("DELTA ORDERS TEST PASSED" if not fails else f"DELTA ORDERS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
