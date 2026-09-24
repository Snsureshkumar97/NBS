#!/usr/bin/env python3
"""Live Delta Exchange India orders that follow a Bitcoin ticket - against a
fake Delta only. What matters most: the right contract and count, the stop
(resting at Delta since 24 Sep 2026, with the tool's own watch behind it - sections
1-8 and 11 run the tool-only path, the one used whenever Delta refuses a stop;
sections 12 on run the stop at Delta), never selling more than is held, the sell
before settlement, the wallet check, and what a restart does.
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

do.VENUE_STOP = False        # sections 1-8 and 11: the tool watches the stop (the fallback path); 12 on turn it back on

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SYM = "P-BTC-80000-250926"
delta_provider.DeltaDataProvider.option_instrument = lambda self, k, strike, kind, expiry=None: (
    None if int(float(strike)) == 12345 else f"{'C' if kind == 'CE' else 'P'}-BTC-{int(float(strike))}-250926")


class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Timeout(Exception):
    pass


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
        # Stop orders (nothing about Delta's real replies to these has been seen live; these
        # are the shapes and failures the executor has to survive).
        self.raise_on_stop = []         # raised, one per stop placement, before anything is created
        self.stop_lag = 0               # this many stop placements refused: "no position yet to reduce"
        self.lose_reply = 0             # stop placements that ARRIVE but whose reply is lost (a timeout)
        self.reply_no_id = 0            # stop placements answered with a reply that names no order id
        self.edit_mode = "ok"           # "ok", "raise" (refuses), "ignore" (says fine, changes nothing)
        self.stop_read_error = False    # reading a stop order fails
        self.cancel_stop_fails = 0      # this many cancels of a stop order fail (it stays)
        self.omit_unfilled = False      # a stop order's reply carries no unfilled_size (it would read as fully filled)

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
        stop = kw.get("stop_order_type") is not None
        if stop:
            if self.raise_on_stop:
                raise self.raise_on_stop.pop(0)
            if self.stop_lag > 0:
                self.stop_lag -= 1
                raise Refused("no_position_for_reduce_only")
            if float(kw["stop_price"]) >= self.mark:
                raise Refused("stop_price_would_trigger_immediately")
        elif self.raise_on_place:
            raise self.raise_on_place.pop(0)
        self.n += 1
        oid = 5000 + self.n
        self.orders_[oid] = dict(kw, id=oid, state="pending" if stop else "open", unfilled_size=kw["size"],
                                 average_fill_price=None)
        if stop and self.lose_reply > 0:
            self.lose_reply -= 1
            raise Timeout("read timed out")
        if stop and self.reply_no_id > 0:
            self.reply_no_id -= 1
            return {"state": "pending"}
        return {"id": oid, "state": "pending" if stop else "open"}

    def cancel_order(self, order_id, product_id):
        self.calls.append(("cancel", int(order_id)))
        o = self.orders_[int(order_id)]
        if o.get("stop_order_type") and self.cancel_stop_fails > 0:
            self.cancel_stop_fails -= 1
            raise Timeout("read timed out")
        if o["state"] in ("closed", "cancelled"):
            raise Exception("order already done")
        o["state"] = "cancelled"

    def edit_order(self, order_id, product_id, **kw):
        self.calls.append(("edit", int(order_id), kw))
        if self.edit_mode == "raise":
            raise Refused("stop_price_not_editable")
        o = self.orders_[int(order_id)]
        if o["state"] not in ("open", "pending"):
            raise Refused("order_not_open")
        if self.edit_mode == "ignore":
            return {"id": int(order_id), "state": o["state"]}
        o.update(kw)
        return {"id": int(order_id), "state": o["state"], "stop_price": kw.get("stop_price")}

    def order(self, order_id):
        o = self.orders_[int(order_id)]
        if self.stop_read_error and o.get("stop_order_type"):
            raise Timeout("read timed out")
        keys = ("id", "state", "size", "unfilled_size", "average_fill_price", "client_order_id",
                "side", "stop_price", "stop_order_type", "reduce_only")
        if self.omit_unfilled and o.get("stop_order_type"):
            keys = tuple(k for k in keys if k != "unfilled_size")
        return {k: o.get(k) for k in keys}

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

    def trigger(self, oid):
        """The mark reached a stop: it becomes a limit order on the book."""
        o = self.orders_[int(oid)]
        assert o["state"] == "pending", o["state"]
        o["state"] = "open"

    def places(self, **match):
        return [kw for c in self.calls if c[0] == "place" for kw in [c[1]] if all(kw.get(k) == v for k, v in match.items())]

    def stops(self):
        return [kw for kw in self.places(side="sell") if kw.get("stop_order_type")]

    def sells(self):
        """Sells that are not stop orders: the tool's own exits."""
        return [kw for kw in self.places(side="sell") if not kw.get("stop_order_type")]

    def stop_ids(self):
        return [i for i, o in self.orders_.items() if o.get("stop_order_type")]

    def order_of(self, kind="sell"):
        return [o for o in self.orders_.values() if o["side"] == kind and not o.get("stop_order_type")]

    def at(self, what, oid=None):
        """Index of the first call of this kind (for the order of events)."""
        for i, c in enumerate(self.calls):
            if c[0] == what and (oid is None or c[1] == oid):
                return i
        return None


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
fk.raise_on_place.append(Timeout("read timed out"))
ex.on_ticket_event("opened", ticket())
drain(ex)
pos = ex.positions[ticket()["trade_id"]]
check("an unanswered entry is looked for by its tag, not taken as failed", pos["state"] == "placing")
clk.advance(do.PLACING_WAIT_S + 1)
drain(ex)
check("...and, absent at Delta, failed without a phantom position", pos["state"] == "failed")

print("9. WHAT THE PAGE AND THE AI DESK SEE")
do.VENUE_STOP = True
check("the stop at Delta is ON by default in the module (this file switches it off for the tool-only sections)",
      open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "delta_orders.py")).read().count("\nVENUE_STOP = True") == 1)
ex, fk, clk, closed = rig(ai=("BTC",))
p = ex.public()
check("the same shape as the Zerodha executor, and it says a stop rests at the venue",
      set(p) >= {"enabled", "enabled_ai", "positions", "notes", "entries_today", "max_entries"}
      and p["venue"] == "Delta Exchange India" and p["stop_at_venue"] is True and p["enabled_ai"]["BTC"] is True)
check("switching on says the stop goes to Delta, and the tool watches it if Delta will not take it",
      "stop order at Delta" in ex.notes[0]["text"] and "watches the stop instead" in ex.notes[0]["text"], ex.notes[0]["text"])
ex.set_enabled("BTC", False)
check("switching off does not say ON", ex.notes[0]["text"].startswith("Live Delta orders for BTC switched OFF"), ex.notes[0]["text"])
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "delta_orders.py")).read()
check("the stop order Delta is sent: reduce-only stop-loss, triggered on the MARK, a limit under it",
      'stop_order_type="stop_loss_order"' in SRC and 'stop_trigger_method="mark_price"' in SRC and "reduce_only=True" in SRC
      and "stop_price=str(stop)" in SRC and "limit_price=str(limit)" in SRC)

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
    check("keys and always-on: switched on, and the page gets the executor's state: Delta as the venue, the stop at it",
          d["ok"] and ex.enabled["BTC"] and d["live"]["stop_at_venue"] is True and d["live"]["venue"] == "Delta Exchange India")
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
import re
WSJ = re.sub(r'"\s*\n\s*\+\s*"', "", WS)          # the dialogs' strings, joined as the browser joins them
check("both confirm dialogs tell a Bitcoin user the stop order goes to Delta, that it is new and unseen, where to watch it, "
      "and that the tool's watch is the only stop if Delta refuses it",
      WSJ.count("THE STOP ORDER GOES TO DELTA") == 2 and WSJ.count("has not yet been seen working on a real Delta account") == 2
      and WSJ.count("Orders > Stop Orders on Delta") == 2 and WSJ.count("only this tool watches the stop") == 2
      and "DELTA HOLDS NO STOP ORDER" not in WS
      and "Place REAL orders on Bitcoin at Delta Exchange India" in WS
      and "Place REAL Delta Exchange orders for the AI desk's Bitcoin trades" in WS)

check("the live-status line words itself by venue - on the Signal card and the AI tab - and never claims a stop at Delta unless one rests there",
      "function liveStateText(p, onDelta)" in WS and 'liveStateText(p, (L.venue || "").indexOf("Delta") === 0)' in WS
      and 'liveStateText(lp, (d.live_venue || "").indexOf("Delta") === 0)' in WS
      and WS.count("stop-loss order at Zerodha, trigger") == 1 and WS.count("stop-loss order at Delta, trigger") == 1
      and "is NOT at Delta - this tool watches the mark, so it only works while the server is running" in WS)
check("the AI payload carries which venue the live positions are at", 'payload["live_venue"] = pub.get("venue", "Zerodha")' in WS)

import shutil, subprocess
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own wording function", False, "install node")
else:
    f0 = WS.index("function liveStateText(p, onDelta){")
    f1 = WS.index("\n}\n", f0) + 3
    prog = "const assert = require('assert');\n" + WS[f0:f1] + r"""
const base = {tradingsymbol: "P-BTC-80000-250926", filled_qty: 10, avg_price: 1500, stop_trigger: 1200, state: "open"};
let t = liveStateText(Object.assign({}, base, {stop_at_venue: true}), true);
assert(t.includes("stop-loss order at Delta, trigger 1200"), t);
assert(!t.includes("NOT at Delta"), "a stop that rests at Delta is not called missing: " + t);
t = liveStateText(Object.assign({}, base, {stop_at_venue: false}), true);
assert(t.includes("NOT at Delta") && t.includes("only works while the server is running"), t);
assert(!t.includes("stop-loss order at Delta"), "a stop that does not rest at Delta is not claimed: " + t);
t = liveStateText(Object.assign({}, base, {tradingsymbol: "NIFTY26SEP24000CE"}), false);
assert(t.includes("stop-loss order at Zerodha, trigger 1200"), t);
assert(liveStateText({state: "placing"}, true).includes("reached Delta"));
assert(liveStateText({state: "attention", tradingsymbol: "X"}, true).includes("check Delta now"));
assert(liveStateText({state: "attention", tradingsymbol: "X"}, false).includes("check Kite now"));
console.log("ok:wording");
"""
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("the page's own wording function, run for real: Delta with a stop resting / without one, and Zerodha",
          "ok:wording" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-500:])

print("11. THE LEVEL THIS TOOL WATCHES TRAILS AS THE TICKET DOES (the tool-only path, used when Delta refuses a stop)")
do.VENUE_STOP = False
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


# =============================================================================
print("12. THE STOP RESTS AT DELTA (24 Sep 2026)")
do.VENUE_STOP = True


def sell_idx(fk):
    """Index, in the call log, of the first sell that is not a stop order."""
    for i, c in enumerate(fk.calls):
        if c[0] == "place" and c[1].get("side") == "sell" and not c[1].get("stop_order_type"):
            return i
    return None


def live_stops(fk):
    return [o for o in fk.orders_.values() if o.get("stop_order_type") and o["state"] in ("open", "pending")]


def public_pos(ex, t):
    return [p_ for p_ in ex.public()["positions"] if p_["trade_id"] == t["trade_id"]][0]


ex, fk, clk, closed = rig()
t, buy = open_and_fill(ex, fk, clk, price=1505.0)            # the ticket's stop is 1200
pos = ex.positions[t["trade_id"]]
st = fk.stops()
check("the moment the buy is held, ONE reduce-only stop-loss sell goes to Delta: trigger 1200 on the MARK, a limit 5% under it, all 10",
      len(st) == 1 and st[0]["size"] == 10 and st[0]["reduce_only"] is True and st[0]["order_type"] == "limit_order"
      and st[0]["stop_order_type"] == "stop_loss_order" and st[0]["stop_trigger_method"] == "mark_price"
      and float(st[0]["stop_price"]) == 1200.0 and 1139.9 <= float(st[0]["limit_price"]) <= 1140.0
      and st[0]["client_order_id"].startswith(pos["tag"] + "S") and st[0]["product_id"] == 777, st)
sid = fk.stop_ids()[-1]
check("it rests (pending, untriggered); the position knows its id; the page is told a stop rests at Delta",
      fk.orders_[sid]["state"] == "pending" and pos["stop_order_id"] == str(sid) and pos["state"] == "open"
      and public_pos(ex, t)["stop_at_venue"] is True and public_pos(ex, t)["stop_order_id"] == str(sid))
check("no other sell of any kind was sent", not fk.sells() and len(fk.places()) == 2)
check("it says so", any("Stop resting at Delta" in n["text"] for n in ex.notes), [n["text"] for n in ex.notes[:3]])
fk.mark = 1300.0
for _ in range(3):
    clk.advance(1)
    drain(ex)
check("the mark falling towards the stop but staying above it changes nothing: one stop, still pending, no sells",
      len(fk.stops()) == 1 and fk.orders_[sid]["state"] == "pending" and not fk.sells() and pos["state"] == "open")
rows = [json.loads(line) for line in open(ex.raw_path)]
check("Delta's raw replies are kept to check the shapes against the real thing: the placement and the state it showed",
      {"stop_placed", "stop_state"} <= {r["kind"] for r in rows} and all(r["trade_id"] == t["trade_id"] for r in rows), rows[:2])

print("12a. DELTA FIRES THE STOP")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.mark = 1199.0
fk.trigger(sid)
drain(ex)
check("triggered but not yet filled: the tool sells nothing and the ticket is still open",
      not fk.sells() and pos["state"] == "open" and not closed)
fk.fill(sid, price=1150.0)
drain(ex)
check("filled at Delta: closed at Delta's price, the ticket closed as a stop-out BY THE VENUE STOP, and the tool sent no sell at all",
      pos["state"] == "closed" and pos["exit_price"] == 1150.0 and not fk.sells() and closed
      and closed[-1][1] == "CLOSED — stop-loss hit (live order, Delta stop)" and pos["stop_order_id"] is None, (pos["state"], closed))
drain(ex)                                                     # the next pass writes the fill row
rows = do.read_fills(ex.log_path)
check("its real fill is logged: 10 contracts, 1150 out, gross (1150 - 1505) x 10 x 0.001",
      len(rows) == 1 and rows[0]["qty"] == 10 and rows[0]["exit_avg"] == 1150.0
      and rows[0]["gross_pnl"] == round((1150.0 - 1505.0) * 10 * 0.001, 4), rows)
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit (live order, Delta stop)"))
drain(ex)
for _ in range(3):
    clk.advance(do.REPRICE_S + 1)
    drain(ex)
check("the ticket's own close arriving afterwards changes nothing: still one row, still no sell, still closed",
      not fk.sells() and pos["state"] == "closed" and len(do.read_fills(ex.log_path)) == 1)

print("12b. TRIGGERED, BUT ITS LIMIT DOES NOT FILL")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.mark = 1150.0
fk.trigger(sid)
drain(ex)
clk.advance(do.REPRICE_S - 1)
drain(ex)
check("triggered and resting as a limit for less than the reprice time: left alone", not fk.sells() and pos["state"] == "open")
clk.advance(2)
drain(ex)
s = fk.sells()
check("still unfilled after that: the stop is cancelled FIRST, then the tool sells all 10 (reduce-only), and the ticket is a stop-out",
      len(s) == 1 and s[0]["size"] == 10 and s[0]["reduce_only"] is True and fk.at("cancel", sid) is not None
      and fk.at("cancel", sid) < sell_idx(fk) and fk.orders_[sid]["state"] == "cancelled"
      and closed and "stop-loss hit" in closed[-1][1] and "did not fill" in (pos.get("exit_reason") or ""), (s, pos.get("exit_reason")))
check("...and Delta's stop is not tried again for this position", pos.get("stop_venue") is False and len(fk.stops()) == 1)
fk.fill([i for i, o in fk.orders_.items() if o["side"] == "sell" and not o.get("stop_order_type")][-1], price=1140.0)
drain(ex)
check("sold: closed at the tool's fill", pos["state"] == "closed" and pos["exit_price"] == 1140.0)

print("12c. THE STOP FILLS ONLY PART, THEN THE REST OF IT IS STUCK")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.mark = 1150.0
fk.trigger(sid)
fk.fill(sid, qty=4, price=1190.0)
drain(ex)
check("4 of 10 filled by the stop: counted, 6 still held", pos["exit_filled"] == 4 and ex._held(pos) == 6 and pos["state"] == "open")
clk.advance(do.REPRICE_S + 1)
drain(ex)
s = fk.sells()
check("the stuck rest is cancelled and ONLY the 6 held are sold (never the 10), reduce-only",
      len(s) == 1 and s[0]["size"] == 6 and s[0]["reduce_only"] is True and fk.at("cancel", sid) < sell_idx(fk), s)
fk.fill([i for i, o in fk.orders_.items() if o["side"] == "sell" and not o.get("stop_order_type")][-1], price=1100.0)
drain(ex)
drain(ex)                                                     # the next pass writes the fill row
rows = do.read_fills(ex.log_path)
check("closed, with the weighted price of both: (4 x 1190 + 6 x 1100) / 10 = 1136",
      pos["state"] == "closed" and len(rows) == 1 and rows[0]["exit_avg"] == 1136.0 and rows[0]["exit_qty_priced"] == 10, rows)

ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.mark = 1150.0
fk.trigger(sid)
fk.fill(sid, qty=4, price=1190.0)
drain(ex)
fk.fill(sid, qty=7, price=(4 * 1190.0 + 3 * 1160.0) / 7)      # Delta's average is over everything filled so far
drain(ex)
check("a stop that fills in pieces (4, then 3 more): each piece counted once, at its own price",
      pos["exit_filled"] == 7 and pos["exit_priced"] == 7 and abs(pos["exit_value"] - (4 * 1190.0 + 3 * 1160.0)) < 1e-6
      and ex._held(pos) == 3, (pos["exit_filled"], pos["exit_priced"], pos["exit_value"]))
clk.advance(do.REPRICE_S + 1)
drain(ex)
fk.fill([i for i, o in fk.orders_.items() if o["side"] == "sell" and not o.get("stop_order_type")][-1], price=1100.0)
drain(ex)
drain(ex)
rows = do.read_fills(ex.log_path)
check("...and the sale of the last 3 gives the true average: (4 x 1190 + 3 x 1160 + 3 x 1100) / 10 = 1154",
      pos["state"] == "closed" and rows and abs(rows[0]["exit_avg"] - 1154.0) < 1e-3 and rows[0]["exit_qty_priced"] == 10, rows)

print("12d. THE MARK IS AT THE STOP AND DELTA'S OWN STOP HAS NOT FIRED")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.mark = 1190.0
drain(ex)
check("at the stop, but Delta's own stop is given time first: no sell yet, ticket open", not fk.sells() and pos["state"] == "open" and not closed)
clk.advance(do.STOP_GRACE_S - 1)
drain(ex)
check("...still inside the grace", not fk.sells() and pos["state"] == "open")
clk.advance(2)
drain(ex)
check("after the grace, still pending at Delta: the stop is cancelled first, then the tool sells all 10, and the ticket closes on the mark",
      len(fk.sells()) == 1 and fk.at("cancel", sid) < sell_idx(fk) and fk.orders_[sid]["state"] == "cancelled"
      and closed and "Delta mark" in closed[-1][1], (fk.sells(), closed))
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
fk.mark = 1190.0
drain(ex)                                                   # first dip: the clock starts
clk.advance(3)
fk.mark = 1400.0
drain(ex)                                                   # recovered
clk.advance(4)
fk.mark = 1190.0
drain(ex)                                                   # dips again: 7 s after the FIRST dip, 4 s of it recovered
check("a dip that recovers before the grace ends starts the clock afresh next time: nothing sold at 7 s from the first dip",
      not fk.sells() and pos["state"] == "open")
clk.advance(do.STOP_GRACE_S)
drain(ex)
check("...and it does sell once the second dip has lasted the grace", len(fk.sells()) == 1)

print("12e. THE TICKET CLOSES: THE STOP COMES OFF BEFORE ANYTHING IS SOLD")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.mark = 1900.0
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
check("target: the stop is cancelled at Delta BEFORE the sell; one reduce-only sell of all 10; no second sell however often the close is heard",
      fk.orders_[sid]["state"] == "cancelled" and len(fk.sells()) == 1 and fk.sells()[0]["size"] == 10
      and fk.sells()[0]["reduce_only"] is True and fk.at("cancel", sid) < sell_idx(fk) and pos["stop_order_id"] is None
      and public_pos(ex, t)["stop_at_venue"] is False)
fk.fill([i for i, o in fk.orders_.items() if o["side"] == "sell" and not o.get("stop_order_type")][-1], price=1890.0)
drain(ex)
drain(ex)
check("closed at the target fill", pos["state"] == "closed" and pos["exit_price"] == 1890.0
      and do.read_fills(ex.log_path)[0]["exit_avg"] == 1890.0)

# the ticket closes at the very moment the stop filled at Delta
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.mark = 1190.0
fk.trigger(sid)
fk.fill(sid, price=1185.0)
ex.on_ticket_event("closed", dict(t, status="CLOSED — stop-loss hit"))
drain(ex)
drain(ex)
check("the stop had already filled when the ticket's close arrives: it is read back, counted, and NOTHING more is sold",
      pos["state"] == "closed" and not fk.sells() and pos["exit_price"] == 1185.0 and ex._held(pos) == 0
      and do.read_fills(ex.log_path)[0]["exit_avg"] == 1185.0, (pos["state"], fk.sells()))

print("12f. THE STOP MOVES UP AT DELTA WHEN THE TICKET TRAILS (after T1)")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
ex.on_ticket_event("trailed", dict(t, premium_sl=1350.0))
drain(ex)
e = [c for c in fk.calls if c[0] == "edit"]
check("the ticket trailed: the stop at Delta is EDITED to the new level - not cancelled and re-placed",
      len(e) == 1 and e[0][1] == sid and e[0][2]["stop_price"] == "1350.0" and 1282.4 <= float(e[0][2]["limit_price"]) <= 1282.5
      and len(fk.stops()) == 1 and ("cancel", sid) not in fk.calls, e)
check("the order at Delta and the tool's own level both read 1350, and it says the stop was moved at Delta",
      float(fk.orders_[sid]["stop_price"]) == 1350.0 and pos["stop_trigger"] == 1350.0 and pos["venue_stop"] == 1350.0
      and any("moved at Delta" in n["text"] for n in ex.notes), [n["text"] for n in ex.notes[:2]])
ex.on_ticket_event("trailed", dict(t, premium_sl=1300.0))
drain(ex)
check("a lower level later never moves it back: no second edit", len([c for c in fk.calls if c[0] == "edit"]) == 1)
ex.on_ticket_event("trailed", dict(t, premium_sl=1450.0))
drain(ex)
check("a higher rung moves it again", len([c for c in fk.calls if c[0] == "edit"]) == 2 and float(fk.orders_[sid]["stop_price"]) == 1450.0)
fk.mark = 1440.0
drain(ex)
fk.trigger(sid)
fk.fill(sid, price=1430.0)
drain(ex)
check("and the trailed stop, filled at Delta, closes the trade at that level - well above the original 1200",
      pos["state"] == "closed" and pos["exit_price"] == 1430.0 and not fk.sells()
      and closed and "stop-loss hit" in closed[-1][1])

print("12g. DELTA WILL NOT TAKE THE EDIT")
for mode in ("raise", "ignore"):
    ex, fk, clk, closed = rig()
    fk.edit_mode = mode
    t, _ = open_and_fill(ex, fk, clk, price=1505.0)
    pos = ex.positions[t["trade_id"]]
    old = fk.stop_ids()[-1]
    ex.on_ticket_event("trailed", dict(t, premium_sl=1350.0))
    drain(ex)
    live = live_stops(fk)
    check(f"edit {'refused' if mode == 'raise' else 'accepted but not applied (read back and caught)'}: the old stop is cancelled and a new one placed at 1350 - "
          "never two resting, never none",
          fk.orders_[old]["state"] == "cancelled" and len(live) == 1 and float(live[0]["stop_price"]) == 1350.0
          and pos["stop_order_id"] == str(live[0]["id"]) and len(fk.stops()) == 2 and fk.at("cancel", old) < [
              i for i, c in enumerate(fk.calls) if c[0] == "place" and c[1].get("stop_order_type") and float(c[1]["stop_price"]) == 1350.0][0],
          (mode, [(o["id"], o["state"], o.get("stop_price")) for o in fk.orders_.values() if o.get("stop_order_type")]))
ex, fk, clk, closed = rig()
fk.edit_mode = "raise"
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
fk.raise_on_stop.extend([Refused("stop_orders_not_supported"), Refused("stop_orders_not_supported")])
ex.on_ticket_event("trailed", dict(t, premium_sl=1350.0))
drain(ex)
check("edit refused AND the new stop refused: nothing rests at Delta, and the page says so",
      not live_stops(fk) and pos["stop_order_id"] is None and public_pos(ex, t)["stop_at_venue"] is False and pos["stop_trigger"] == 1350.0)
fk.mark = 1340.0
drain(ex)
check("...so the tool's own watch sells at once at the trailed level (no grace - Delta has no stop to give time to)",
      len(fk.sells()) == 1 and closed and "Delta mark" in closed[-1][1], (fk.sells(), closed))
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.trigger(sid)
fk.fill(sid, price=1190.0)                                    # it fired and filled just as the ticket trailed
ex.on_ticket_event("trailed", dict(t, premium_sl=1350.0))
drain(ex)
check("a trail arriving for a stop that has already filled: read back, closed as a stop-out, no new stop placed, nothing sold",
      pos["state"] == "closed" and len(fk.stops()) == 1 and not fk.sells() and closed and "stop-loss hit" in closed[-1][1])

print("12h. DELTA REFUSES THE STOP - THE TOOL'S OWN WATCH TAKES OVER")
ex, fk, clk, closed = rig()
fk.raise_on_stop.extend([Refused("stop_orders_not_supported_for_options")] * 10)
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
check("first refusal: it will try again", pos.get("stop_venue") is None and pos["stop_tries"] == 1 and pos["state"] == "open")
drain(ex)
drain(ex)
check("...but not at once: the retries are spaced STOP_RETRY_S apart, not one per second-long pass", pos["stop_tries"] == 1 and len(fk.stops()) == 1)
for _ in range(6):
    clk.advance(do.STOP_RETRY_S + 1)
    drain(ex)
check(f"refused {do.STOP_TRIES} times, spaced apart: it stops asking, says so as an error, and says it only works while the server runs",
      len(fk.stops()) == do.STOP_TRIES and pos.get("stop_venue") is False
      and any(n["level"] == "error" and "watches the stop" in n["text"] and "only works while the server is running" in n["text"]
              for n in ex.notes), [n["text"] for n in ex.notes[:2]])
check("the position is open, with no stop at Delta, and the page is told so", pos["state"] == "open"
      and public_pos(ex, t)["stop_at_venue"] is False)
ex.on_ticket_event("trailed", dict(t, premium_sl=1350.0))
drain(ex)
check("a trail now moves only the level the tool watches, and does not ask Delta again",
      pos["stop_trigger"] == 1350.0 and len(fk.stops()) == do.STOP_TRIES and not [c for c in fk.calls if c[0] == "edit"]
      and any("this tool watches it" in n["text"] for n in ex.notes), [n["text"] for n in ex.notes[:2]])
fk.mark = 1340.0
drain(ex)
check("the mark at that level: the tool sells everything at once, reduce-only - the tool-only path is intact",
      len(fk.sells()) == 1 and fk.sells()[0]["size"] == 10 and fk.sells()[0]["reduce_only"] is True
      and closed and "Delta mark" in closed[-1][1])
ex, fk, clk, closed = rig()
fk.raise_on_stop.extend([Refused("x")] * 10)
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
check("a ticket that closes while the stop was never placed: just the one sell, nothing to cancel",
      len(fk.sells()) == 1 and not [c for c in fk.calls if c[0] == "cancel"])

print("12i. DELTA'S POSITION LAGS THE FILL; A LOST REPLY; A REPLY WITH NO ID")
ex, fk, clk, closed = rig()
fk.stop_lag = 2
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
check("'no position yet to reduce' on the first try: not given up on", pos.get("stop_order_id") is None and pos["stop_tries"] == 1)
for _ in range(2):
    clk.advance(do.STOP_RETRY_S + 1)
    drain(ex)
check("...the third try, seconds later, rests the stop", pos.get("stop_order_id") and len(live_stops(fk)) == 1 and pos["stop_tries"] == 3)
for kind in ("lose_reply", "reply_no_id"):
    ex, fk, clk, closed = rig()
    setattr(fk, kind, 1)
    t, _ = open_and_fill(ex, fk, clk, price=1505.0)
    pos = ex.positions[t["trade_id"]]
    check(f"{kind}: the stop reached Delta but the tool does not know its id yet", pos.get("stop_order_id") is None and len(fk.stops()) == 1)
    clk.advance(do.STOP_RETRY_S + 1)
    drain(ex)
    if kind == "reply_no_id":
        check("reply_no_id: it says the reply named no order id - and that is a note, not an unexpected error",
              any("names no order id" in n["text"] for n in ex.notes) and not any("Could not check" in n["text"] for n in ex.notes),
              [n["text"] for n in ex.notes[:3]])
    check(f"{kind}: found again at Delta by its tag and adopted - never a second stop",
          len(fk.stops()) == 1 and len(live_stops(fk)) == 1 and pos["stop_order_id"] == str(live_stops(fk)[0]["id"]), (len(fk.stops()), pos.get("stop_order_id")))

print("12j. THE STOP CANNOT BE READ, OR IS CANCELLED ON DELTA'S SCREEN")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.stop_read_error = True
fk.mark = 1190.0
drain(ex)
check("Delta's stop cannot be read but the mark is at the stop: still inside the grace, and no crash", pos["state"] == "open" and not fk.sells())
clk.advance(do.STOP_GRACE_S + 1)
drain(ex)
check("after the grace the tool sells anyway - not being able to read the stop never disables the exit",
      len(fk.sells()) == 1 and closed and "Delta mark" in closed[-1][1], (fk.sells(), closed))
check("the stop could not be confirmed off Delta, so it is remembered to be cancelled again", pos.get("stop_stray") == str(sid), pos.get("stop_stray"))
fk.stop_read_error = False
clk.advance(do.REPRICE_S + 1)
drain(ex)
check("Delta can be read again: the stray is confirmed gone and forgotten", pos.get("stop_stray") is None and fk.orders_[sid]["state"] == "cancelled")

for how in ("cancelled", "closed unfilled"):
    ex, fk, clk, closed = rig()
    t, _ = open_and_fill(ex, fk, clk, price=1505.0)
    pos = ex.positions[t["trade_id"]]
    sid = fk.stop_ids()[-1]
    fk.orders_[sid]["state"] = "cancelled" if how == "cancelled" else "closed"
    drain(ex)
    check(f"the stop shows '{how}' at Delta with no fill (you cancelled it, or Delta did): the tool says so as an error and takes the watch itself",
          pos.get("stop_venue") is False and pos["stop_order_id"] is None and public_pos(ex, t)["stop_at_venue"] is False
          and any(n["level"] == "error" and "watches the stop" in n["text"] for n in ex.notes), [n["text"] for n in ex.notes[:2]])
    for _ in range(3):
        clk.advance(do.STOP_RETRY_S + 1)
        drain(ex)
    check(f"'{how}': it does not fight you by placing another", len(fk.stops()) == 1 and pos["state"] == "open")
    fk.mark = 1190.0
    drain(ex)
    check(f"'{how}': and the mark at the stop sells at once", len(fk.sells()) == 1)
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.orders_[sid]["state"] = "untriggered"                    # a word this tool has never seen
drain(ex)
drain(ex)
check("a state it does not know: said once, position untouched, still watched",
      pos["state"] == "open" and sum(1 for n in ex.notes if "does not know" in n["text"]) == 1)

print("12k. A STOP THAT WILL NOT CANCEL")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.cancel_stop_fails = 2
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
check("the cancel fails and the stop is still resting: the sell is sent anyway (reduce-only) and the stop is remembered",
      len(fk.sells()) == 1 and pos.get("stop_stray") == str(sid) and fk.orders_[sid]["state"] == "pending")
for _ in range(3):
    clk.advance(do.REPRICE_S + 1)
    drain(ex)
check("cancelled again on the next passes until it works", fk.orders_[sid]["state"] == "cancelled" and pos.get("stop_stray") is None)
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.cancel_stop_fails = 99
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
for _ in range(do.STOP_CANCEL_TRIES + 3):
    clk.advance(do.REPRICE_S + 1)
    drain(ex)
check("never cancellable: after STOP_CANCEL_TRIES it stops trying and tells the user, as an error, to cancel it on Delta",
      pos.get("stop_stray") is None and any(n["level"] == "error" and "CANCEL IT ON DELTA" in n["text"] for n in ex.notes),
      [n["text"] for n in ex.notes[:2]])

print("12l. THE OTHER WAYS OUT: SOLD ELSEWHERE, EXPIRY, A RESTART")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.manual_sold = 10
for _ in range(2):
    clk.advance(do.VERIFY_S + 1)
    drain(ex)
check("all sold on Delta's own screen (two readings): the resting stop is taken off Delta and nothing is sent",
      pos["state"] == "closed" and fk.orders_[sid]["state"] == "cancelled" and not fk.sells())
ex, fk, clk, closed = rig()
fk.settle = "2026-09-20T07:00:00Z"
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
sid = fk.stop_ids()[-1]
clk.advance(61 * 60)
drain(ex)
check("thirty minutes before settlement: the stop is cancelled first, then the sell", len(fk.sells()) == 1
      and fk.orders_[sid]["state"] == "cancelled" and fk.at("cancel", sid) < sell_idx(fk) and closed and "settles" in closed[-1][1])
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
sid = fk.stop_ids()[-1]
ex2 = do.Executor("me@example.invalid", ex.log_path, client=lambda: fk, close_ticket=lambda i, s: None,
                  now=lambda: clk.now, clock=lambda: clk.t, mark=lambda sym: fk.mark, start=False)
check("the stop's id survives a restart on disk", ex2.positions[t["trade_id"]].get("stop_order_id") == str(sid))
drain(ex2)
check("a restart with a position and a stop at Delta: the stop is cancelled first, then the position sold",
      fk.orders_[sid]["state"] == "cancelled" and len(fk.sells()) == 1 and fk.at("cancel", sid) < sell_idx(fk))

print("12m. WHEN NO STOP SHOULD BE PLACED")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1100.0)               # filled BELOW the 1200 stop
check("filled at or below the stop: no stop order (it would trigger at once) - sold", not fk.stops() and len(fk.sells()) == 1)
ex, fk, clk, closed = rig()
fk.mark = 1190.0
t, _ = open_and_fill(ex, fk, clk, price=1500.0)                # the mark is already at the stop when the fill lands
pos = ex.positions[t["trade_id"]]
check("the mark already at the stop when it is held: no stop order is placed - and next second the tool sells",
      not fk.stops() and any("already at the stop" in n["text"] for n in ex.notes))
drain(ex)
check("...sells all 10", len(fk.sells()) == 1 and fk.sells()[0]["size"] == 10)
ex, fk, clk, closed = rig()
t = ticket()
ex.on_ticket_event("opened", t)
drain(ex)
buy = [o for o in fk.orders_.values() if o["side"] == "buy"][-1]
fk.fill(buy["id"], qty=6, price=1500.0)
clk.advance(do.FILL_WAIT_S + 1)
drain(ex)
check("a partly filled entry (6 of 10): the stop is for the 6 held, not the 10 ordered",
      len(fk.stops()) == 1 and fk.stops()[0]["size"] == 6, fk.stops())
do.VENUE_STOP = False
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
check("the module switch off (VENUE_STOP): no stop order is ever sent, and the page says none rests at Delta",
      not fk.stops() and ex.public()["stop_at_venue"] is False and public_pos(ex, t)["stop_at_venue"] is False)
do.VENUE_STOP = True

print("12o. DELTA'S WORDS FOR A STOP ORDER HAVE NOT BEEN SEEN LIVE: TWO WAYS TO MISREAD THEM")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.orders_[sid]["state"] = "open"                          # Delta calls an UNtriggered stop 'open'
fk.mark = 1500.0
for _ in range(4):
    clk.advance(do.REPRICE_S + 1)
    drain(ex)
check("an untriggered stop that Delta labels 'open', the mark far above it: it is only resting - not cancelled, nothing sold, still at Delta",
      ("cancel", sid) not in fk.calls and not fk.sells() and pos["state"] == "open" and pos["stop_order_id"] == str(sid)
      and public_pos(ex, t)["stop_at_venue"] is True and len(fk.stops()) == 1, (pos["state"], fk.calls[-3:]))
fk.mark = 1190.0
drain(ex)
clk.advance(do.REPRICE_S + 1)
drain(ex)
check("...but 'open' with the mark AT the stop is a triggered stop that has not filled: cancelled, then sold by the tool",
      ("cancel", sid) in fk.calls and len(fk.sells()) == 1 and fk.at("cancel", sid) < sell_idx(fk), (fk.sells(),))
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
sid = fk.stop_ids()[-1]
fk.mark = 1150.0
fk.trigger(sid)
fk.fill(sid, qty=4, price=1190.0)
drain(ex)
fk.mark = 1300.0                                            # the price bounced back above the stop after the partial fill
clk.advance(do.REPRICE_S + 1)
drain(ex)
check("a stop that has already filled part is a triggered stop even if the mark has since bounced above it: the rest is cancelled and the 6 held are sold",
      len(fk.sells()) == 1 and fk.sells()[0]["size"] == 6 and fk.at("cancel", sid) < sell_idx(fk), fk.sells())
ex, fk, clk, closed = rig()
fk.omit_unfilled = True
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
pos = ex.positions[t["trade_id"]]
for _ in range(3):
    clk.advance(1)
    drain(ex)
check("a stop order whose reply has no unfilled_size (it would read as FULLY FILLED): nothing is believed sold - the position stays 10, open, its stop resting",
      pos["state"] == "open" and ex._held(pos) == 10 and not pos.get("exit_filled") and pos["stop_order_id"] and not closed
      and not fk.sells(), (pos["state"], ex._held(pos), pos.get("exit_filled")))
sid = fk.stop_ids()[-1]
ex.on_ticket_event("closed", dict(t, status="CLOSED — T1 hit"))
drain(ex)
check("...and the target still sells all 10 (the stop cancelled first)", len(fk.sells()) == 1 and fk.sells()[0]["size"] == 10
      and fk.at("cancel", sid) < sell_idx(fk))

print("12n. ONE STOP PER POSITION, ACROSS EVERY WAY IT COULD BE DOUBLED")
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk, price=1505.0)
for _ in range(10):
    clk.advance(do.STOP_RETRY_S + 1)
    drain(ex)
    ex.on_ticket_event("trailed", dict(t, premium_sl=1200.0))
    drain(ex)
check("many polls and a trail that is no improvement: still exactly one stop order ever sent", len(fk.stops()) == 1 and len(live_stops(fk)) == 1)

print("DELTA ORDERS TEST PASSED" if not fails else f"DELTA ORDERS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
