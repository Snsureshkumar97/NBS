#!/usr/bin/env python3
"""exness_orders.Executor when MetaApi does not answer (7 Oct 2026: its London servers timed out all afternoon and a gold
order was lost - "why did gold didnt take live order" -> "yes fix it"). Against a scripted fake MetaApi - no network,
no token, no order anywhere:
  - a READ that times out (or gets MetaApi's own 5xx) is tried once more; a second failure, or any other error, is not
  - an ORDER is never sent twice on a timeout: Exness is looked at - adopted if it went through, otherwise watched for
    (UNCONFIRMED_WATCH_S) and adopted if it arrives late, closed at once if its ticket closed meanwhile
  - an order MetaApi ANSWERED with a 5xx was turned down, not lost: sent once more
  - a read after a placed order failing never marks a live position "failed"
  - a position the tool did not open (another comment) is never adopted"""
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["TRADING_TOOL_LOGS"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests

import exness_orders as eo

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

CLOCK = {"t": 1791336000.0}                                  # epoch seconds
TIMEOUT = lambda: requests.ReadTimeout("HTTPSConnectionPool(host='mt-client-api-v1.london.agiliumtrade.ai'): Read timed out.")
HTTP504 = lambda: eo.ExnessOrderError("MetaApi HTTP 504: It seems like the account is not connected to broker yet")


class Fake:
    """Scripted: `fail[name]` is a list of exceptions raised by that call, in order, before it answers normally.
    trade_mode: "ok" | "timeout_placed" (carried out, then no answer) | "timeout_lost" (no answer, nothing done)."""
    def __init__(self):
        self.fail, self.calls, self.trades, self.open = {}, [], [], {}
        self.trade_mode, self.next_pid = "ok", 5000
    def _hit(self, name):
        self.calls.append(name)
        q = self.fail.get(name) or []
        if q:
            raise q.pop(0)()
    def symbol(self, base): self._hit("symbol"); return base + "m"
    def spec(self, sym): self._hit("spec"); return {"contractSize": 100, "minVolume": 0.01, "maxVolume": 200, "volumeStep": 0.01, "digits": 3}
    def info(self): self._hit("info"); return {"leverage": 200, "freeMargin": 9900}
    def price(self, sym): self._hit("price"); return {"bid": 4117.10, "ask": 4117.36}
    def _place(self, body):
        self.next_pid += 1
        pid = str(self.next_pid)
        sell = body["actionType"] == "ORDER_TYPE_SELL"
        self.open[pid] = {"id": pid, "symbol": body["symbol"], "type": "POSITION_TYPE_SELL" if sell else "POSITION_TYPE_BUY",
                          "volume": body["volume"], "openPrice": 4117.10, "profit": 0.0, "comment": "TradePicker",
                          "time": dt.datetime.fromtimestamp(CLOCK["t"] + 2, dt.timezone.utc).isoformat().replace("+00:00", "Z")}
        return pid
    def trade(self, body):
        self.trades.append(dict(body))
        self._hit("trade")
        at = body["actionType"]
        if at == "POSITION_CLOSE_ID":
            self.open.pop(body["positionId"], None)
            return {"numericCode": 10009}
        if self.trade_mode == "timeout_placed":
            self._place(body)
            raise TIMEOUT()
        if self.trade_mode == "timeout_lost":
            raise TIMEOUT()
        pid = self._place(body)
        return {"numericCode": 10009, "positionId": pid, "orderId": "o" + pid}
    def position(self, pid): self._hit("position"); return self.open.get(pid)
    def positions(self): self._hit("positions"); return list(self.open.values())
    def deals(self, pid): return [{"entryType": "DEAL_ENTRY_OUT", "price": 4116.0, "profit": 11.0}]


eo.user_exness.account = lambda email, aid: {"id": aid, "kind": "demo", "region": "london"}
SLEPT = []
def executor(fake):
    ex = eo.Executor("t@example.invalid", os.path.join(tempfile.mkdtemp(), "trades.csv"), client_factory=lambda a: fake,
                     start=False, clock=lambda: CLOCK["t"])
    ex.sleep = lambda s: SLEPT.append(s)
    ex.enabled["GOLD"] = True
    ex.acct_for["rule"]["GOLD"] = "acc-demo"
    return ex
n = [0]
def ticket():
    n[0] += 1
    return {"trade_id": f"GOLD-T{n[0]}", "index": "GOLD", "option_type": "PE", "index_sl": 4128.0,
            "index_targets": [4110.0, 4106.0, 4100.0], "exit_at": "T2", "lots": 0.1, "entry_spot": 4117.12}
notes = lambda ex: " | ".join(x["text"] for x in ex.notes)

print("1. A READ THAT TIMES OUT IS TRIED ONCE MORE")
f = Fake(); f.fail["info"] = [TIMEOUT]; ex = executor(f); t = ticket()
ex.handle("opened", t)
p = ex.positions[t["trade_id"]]
check("the account read timed out once: tried again, the order went through", p["state"] == "open" and len(f.trades) == 1, p.get("state"))
check("...after a short pause", eo.RETRY_PAUSE_S in SLEPT)
f = Fake(); f.fail["price"] = [HTTP504]; ex = executor(f); t = ticket()
ex.handle("opened", t)
check("MetaApi's 504 on the price read: tried again too", ex.positions[t["trade_id"]]["state"] == "open")
f = Fake(); f.fail["symbol"] = [TIMEOUT, TIMEOUT]; ex = executor(f); t = ticket()
ex.handle("opened", t)
check("a read that fails twice: not placed, and NO order was sent", ex.positions[t["trade_id"]]["state"] == "failed"
      and not f.trades and "NOT placed" in notes(ex), notes(ex))
f = Fake(); f.fail["spec"] = [lambda: eo.ExnessOrderError("MetaApi HTTP 400: bad symbol")]; ex = executor(f); t = ticket()
ex.handle("opened", t)
check("any other error is not retried", f.calls.count("spec") == 1 and ex.positions[t["trade_id"]]["state"] == "failed")

print("2. AN ORDER METAAPI DID NOT ANSWER")
f = Fake(); f.trade_mode = "timeout_placed"; ex = executor(f); t = ticket()
ex.handle("opened", t)
p = ex.positions[t["trade_id"]]
check("it went through: found at Exness and tracked, sent ONCE", p["state"] == "open" and p["position_id"] in f.open
      and len(f.trades) == 1 and "answered late" in notes(ex), (p.get("state"), len(f.trades)))
check("...with its fill price", p.get("avg_price") == 4117.10)
f = Fake(); f.trade_mode = "timeout_lost"; ex = executor(f); t = ticket()
ex.handle("opened", t)
p = ex.positions[t["trade_id"]]
check("it did not go through: NOT sent again - unconfirmed, and watched for", p["state"] == "unconfirmed" and len(f.trades) == 1
      and "Nothing is sent again" in notes(ex), (p.get("state"), len(f.trades)))
t2 = ticket()
ex.handle("opened", t2)
check("...a new gold signal meanwhile is not stacked on it", len(f.trades) == 1 and "one position at a time" in notes(ex))
f._place(f.trades[0])                                        # it arrives late
CLOCK["t"] += 30
ex.poll()
check("it arrives 30 s later: adopted and tracked", p["state"] == "open" and p.get("position_id") in f.open
      and "arrived at Exness after all" in notes(ex), p.get("state"))
f = Fake(); f.trade_mode = "timeout_lost"; ex = executor(f); t = ticket()
ex.handle("opened", t)
p = ex.positions[t["trade_id"]]
CLOCK["t"] += eo.UNCONFIRMED_WATCH_S + 5
ex.poll()
check(f"it never arrives: given up after {eo.UNCONFIRMED_WATCH_S} s - failed, nothing placed", p["state"] == "failed"
      and "never arrived" in notes(ex), p.get("state"))
f = Fake(); f.trade_mode = "timeout_lost"; ex = executor(f); t = ticket()
ex.handle("opened", t)
p = ex.positions[t["trade_id"]]
ex.handle("closed", t)                                       # the ticket closes on paper while it is unconfirmed
check("its ticket closes meanwhile: remembered", p["state"] == "unconfirmed" and p.get("ticket_closed"))
f._place(f.trades[0])
CLOCK["t"] += 20
ex.poll()
check("...then it arrives: closed at Exness at once, not left running", p["state"] == "closed" and not f.open
      and f.trades[-1]["actionType"] == "POSITION_CLOSE_ID", (p.get("state"), f.open))

print("3. AN ORDER METAAPI TURNED DOWN (AN ANSWER, NOT A SILENCE)")
f = Fake(); f.fail["trade"] = [HTTP504]; ex = executor(f); t = ticket()
ex.handle("opened", t)
p = ex.positions[t["trade_id"]]
check("a 504 on the order, nothing at Exness: sent once more, placed", p["state"] == "open" and len(f.trades) == 2
      and f.calls.count("positions") >= 1, (p.get("state"), len(f.trades)))
f = Fake(); f.fail["trade"] = [HTTP504, HTTP504]; ex = executor(f); t = ticket()
ex.handle("opened", t)
check("...turned down twice: not placed, exactly two attempts", ex.positions[t["trade_id"]]["state"] == "failed" and len(f.trades) == 2)

print("4. A PLACED ORDER STAYS PLACED")
f = Fake(); f.fail["position"] = [TIMEOUT, TIMEOUT]; ex = executor(f); t = ticket()
ex.handle("opened", t)
p = ex.positions[t["trade_id"]]
check("the read after the order fails twice: the position is still OPEN and tracked (it used to say failed)",
      p["state"] == "open" and p.get("position_id") in f.open, p.get("state"))

print("5. NEVER SOMEONE ELSE'S POSITION")
f = Fake(); f.trade_mode = "timeout_lost"; ex = executor(f); t = ticket()
ex.handle("opened", t)
pid = f._place(f.trades[0]); f.open[pid]["comment"] = "manual"          # a trade placed by hand, same symbol/side/size
CLOCK["t"] += 10
ex.poll()
check("a position with another comment is not adopted", ex.positions[t["trade_id"]]["state"] == "unconfirmed")
old = f._place(f.trades[0]); f.open[old]["time"] = "2026-01-01T00:00:00Z"
ex.poll()
check("nor one opened before the order went out", ex.positions[t["trade_id"]]["state"] == "unconfirmed")

print("6. A RESTART")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vm_precheck.py")).read()
check("an unconfirmed order blocks a restart (it may be a real position) - only closed / failed are safe",
      'SAFE_STATES = {".exness_live.json": {"closed", "failed"}}' in src)

print()
print("EXNESS ORDER RETRY TEST PASSED" if not fails else f"EXNESS ORDER RETRY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
