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
        net = 0
        for o in self.orders_.values():
            filled = o["size"] - o["unfilled_size"]
            net += filled if o["side"] == "buy" else -filled
        return [{"product_id": 777, "size": max(0, net - self.manual_sold)}]

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
check("all sold on Delta's own screen: closed here, nothing sent", ex.positions[t["trade_id"]]["state"] == "closed"
      and not fk.places(side="sell"))
ex, fk, clk, closed = rig()
t, _ = open_and_fill(ex, fk, clk)
clk.advance(do.SETTLE_S + 1)
fk.manual_sold = 4
ex.on_ticket_event("closed", dict(t, status="CLOSED — AI exit"))
drain(ex)
check("on exit, only what Delta shows as held is sold", fk.places(side="sell")[0]["size"] == 6)

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

print("DELTA ORDERS TEST PASSED" if not fails else f"DELTA ORDERS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
