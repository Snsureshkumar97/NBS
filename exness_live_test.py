#!/usr/bin/env python3
"""user_exness.py + exness_orders.py - each user's own Exness accounts (demo / real), their
balances, and live orders - against a FAKE MetaApi: no network, no token, no order anywhere."""
import json
import datetime as dt
import os
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["TRADING_TOOL_LOGS"] = tempfile.mkdtemp()
os.environ["ENABLE_CRYPTO"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import accounts
import config
import exness_orders as eo
import real_entry
import user_exness as ux

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


class Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body
        self.content = b"x" if body is not None else b""
    def json(self):
        return self._b


TOKEN = "tok-FAKE-NOT-REAL-987"
DEMO, REAL, RO, FX = "acc-demo-1111", "acc-real-2222", "acc-ro-3333", "acc-icm-4444"
PROV = {DEMO: {"region": "london", "state": "DEPLOYED", "connectionStatus": "CONNECTED", "server": "Exness-MT5Trial11"},
        REAL: {"region": "london", "state": "DEPLOYED", "connectionStatus": "CONNECTED", "server": "Exness-MT5Real8"},
        RO: {"region": "london", "state": "DEPLOYED", "connectionStatus": "CONNECTED", "server": "Exness-MT5Real8"},
        FX: {"region": "london", "state": "DEPLOYED", "connectionStatus": "CONNECTED", "server": "ICMarkets-Live"},
        "acc-off-5555": {"region": "london", "state": "UNDEPLOYED", "connectionStatus": "DISCONNECTED",
                         "server": "Exness-MT5Trial11"}}
INFO = {DEMO: {"type": "ACCOUNT_TRADE_MODE_DEMO", "currency": "USD", "balance": 10000, "equity": 10012.5,
               "freeMargin": 9900, "margin": 100, "leverage": 200, "investorMode": False},
        REAL: {"type": "ACCOUNT_TRADE_MODE_REAL", "currency": "USD", "balance": 250.75, "equity": 250.75,
               "freeMargin": 250.75, "margin": 0, "leverage": 200, "investorMode": False},
        RO: {"type": "ACCOUNT_TRADE_MODE_REAL", "currency": "USD", "balance": 99, "equity": 99, "freeMargin": 99,
             "margin": 0, "leverage": 100, "investorMode": True}}


class FakeMetaApi:
    """Answers the provisioning and client hosts the way MetaApi documents them."""
    def __init__(self):
        self.calls, self.trades, self.positions = [], [], {}
        self.next_pid = 1000
        self.lock = threading.Lock()

    def _auth(self, headers):
        return (headers or {}).get("auth-token") == TOKEN

    def get(self, url, headers=None, timeout=None, params=None):
        return self.request("GET", url, headers=headers, timeout=timeout)

    def request(self, method, url, json=None, headers=None, timeout=None):
        with self.lock:
            self.calls.append((method, url, json, dict(headers or {})))
            if not self._auth(headers):
                return Resp(401, {"message": "bad token"})
            acc = url.split("/accounts/")[1].split("/")[0]
            if "mt-provisioning" in url:
                return Resp(200, PROV[acc]) if acc in PROV else Resp(404, {"message": "nf"})
            if url.endswith("/account-information"):
                return Resp(200, INFO[acc]) if acc in INFO else Resp(504, {"message": "not connected"})
            if url.endswith("/symbols"):
                return Resp(200, ["BTCUSDm", "XAUUSDm", "EURUSDm"])
            if url.endswith("/specification"):
                sym = url.split("/symbols/")[1].split("/")[0]
                return Resp(200, {"symbol": sym, "contractSize": 1 if sym.startswith("BTC") else 100,
                                  "minVolume": 0.01, "maxVolume": 200, "volumeStep": 0.01,
                                  "digits": 2 if sym.startswith("BTC") else 3})
            if url.endswith("/current-price"):
                sym = url.split("/symbols/")[1].split("/")[0]
                return Resp(200, {"bid": 84000.0, "ask": 84010.0} if sym.startswith("BTC")
                            else {"bid": 4140.30, "ask": 4140.56})
            if method == "POST" and url.endswith("/trade"):
                cid = (json or {}).get("clientId")
                if cid is not None and not __import__("re").match(r"^[^_]+_[^_]+_[^_]+$", str(cid)):
                    return Resp(400, {"error": "ValidationError", "message": "Validation failed (abc)",
                                      "details": [{"parameter": "clientId", "message": "Invalid value. Value must "
                                                   "match required pattern."}]})
                self.trades.append((acc, json))
                at = json.get("actionType")
                if at in ("ORDER_TYPE_BUY", "ORDER_TYPE_SELL"):
                    self.next_pid += 1
                    pid = str(self.next_pid)
                    px = 84010.0 if at == "ORDER_TYPE_BUY" else 84000.0
                    self.positions[pid] = {"id": pid, "openPrice": px, "profit": -2.5, "currentPrice": 84005.0,
                                           "stopLoss": json.get("stopLoss"), "takeProfit": json.get("takeProfit")}
                    return Resp(200, {"numericCode": 10009, "stringCode": "TRADE_RETCODE_DONE", "orderId": "o" + pid,
                                      "positionId": pid})
                if at == "POSITION_MODIFY":
                    self.positions[json["positionId"]]["stopLoss"] = json.get("stopLoss")
                    return Resp(200, {"numericCode": 10009, "stringCode": "TRADE_RETCODE_DONE"})
                if at == "POSITION_CLOSE_ID":
                    self.positions.pop(json["positionId"], None)
                    return Resp(200, {"numericCode": 10009, "stringCode": "TRADE_RETCODE_DONE"})
            if "/positions/" in url:
                pid = url.rsplit("/", 1)[1]
                return Resp(200, self.positions[pid]) if pid in self.positions else Resp(404, {"message": "nf"})
            if "/history-deals/position/" in url:
                return Resp(200, [{"entryType": "DEAL_ENTRY_IN", "price": 84010.0, "profit": 0},
                                  {"entryType": "DEAL_ENTRY_OUT", "price": 84300.0, "profit": 72.5, "swap": -4.75,
                                   "reason": self.close_reason}])
            return Resp(404, {"message": "unknown route"})

    close_reason = "DEAL_REASON_CLIENT"


EMAIL = "trader@example.test"
accounts.create_user(EMAIL, "a-long-test-password-1")
fake = FakeMetaApi()

print("1. CONNECTING - CHECKED AGAINST METAAPI BEFORE ANYTHING IS KEPT")
check("a test user exists to connect", accounts.get_user(EMAIL) is not None)
ok, msg = ux.connect(EMAIL, "", DEMO, session=fake)
check("no token -> refused, nothing kept", not ok and "token" in msg.lower() and not ux.stored(EMAIL), msg)
ok, msg = ux.connect(EMAIL, "bad-token", DEMO, session=fake)
check("a refused token -> 401 in plain words", not ok and "refused that token" in msg, msg)
ok, msg = ux.connect(EMAIL, TOKEN, FX, session=fake)
check("a non-Exness account (ICMarkets) is refused", not ok and "not Exness" in msg, msg)
ok, msg = ux.connect(EMAIL, TOKEN, "acc-off-5555", session=fake)
check("an undeployed account is refused with what to do", not ok and "deploy it" in msg, msg)
ok, msg = ux.connect(EMAIL, TOKEN, f"{DEMO}\n{REAL}, {RO}, x-4, y-5", session=fake)
check("more than 3 accounts -> refused", not ok and "Up to 3" in msg, msg)
ok, msg = ux.connect(EMAIL, TOKEN, f"{DEMO}\n{REAL}\n{RO}", session=fake)
check("demo + real + read-only real: connected, each named", ok and "DEMO" in msg and "REAL" in msg and "read-only" in msg, msg)
st = ux.stored(EMAIL)
check("kept: id, region, server, demo/real, investor - and NO token in the account list",
      [a["kind"] for a in st] == ["demo", "real", "real"] and st[2]["investor"] is True
      and all(TOKEN not in json.dumps(a) for a in st), st)
check("the token is kept separately, only token_for() reads it", ux.token_for(EMAIL) == TOKEN)
check("the token went ONLY in the auth-token header, never in a URL",
      all(TOKEN not in c[1] for c in fake.calls) and any(c[3].get("auth-token") == TOKEN for c in fake.calls))

print("2. BALANCES - LIVE, CACHED, DEMO AND REAL APART")
b = ux.balances(EMAIL, session=fake)
check("three accounts with balance, equity, free margin, leverage",
      [round(x["balance"], 2) for x in b] == [10000, 250.75, 99] and b[0]["leverage"] == 200 and b[0]["kind"] == "demo"
      and b[1]["kind"] == "real", [(x["kind"], x["balance"]) for x in b])
n = len(fake.calls)
ux.balances(EMAIL, session=fake)
check(f"asked again within {ux.INFO_TTL}s: no MetaApi call", len(fake.calls) == n)
summ = ux.summary(EMAIL)
check("summary(): connected, accounts, and no token anywhere in it", summ["connected"] and TOKEN not in json.dumps(summ))
os.environ.update({"METAAPI_TOKEN": TOKEN, "METAAPI_ACCOUNT_ID": DEMO, "METAAPI_REGION": "london"})
sh = ux.server_account(session=fake, force=True)
check("the server's shared demo reads as shared + demo (for the admin's page)", sh and sh["shared"] and sh["kind"] == "demo"
      and sh["balance"] == 10000, sh)

print("3. THE LIVE SWITCH NEEDS AN ACCOUNT THAT CAN TRADE")
d = tempfile.mkdtemp()
log = os.path.join(d, "trades.csv")
closed = []
ex = eo.Executor(EMAIL, log, close_ticket=lambda name, status, price=None, trade_id=None: closed.append((name, status, price, trade_id)),
                 client_factory=lambda acct: eo.Client(TOKEN, acct, session=fake), start=False)
try:
    ex.set_enabled("BTC", True, "rule"); err = None
except ValueError as exc:
    err = str(exc)
check("on with no account chosen -> refused", err and "Choose one" in err, err)
try:
    ex.set_enabled("BTC", True, "rule", account_id=RO); err = None
except ValueError as exc:
    err = str(exc)
check("the read-only (investor) account -> refused, says why", err and "investor" in err, err)
try:
    ex.set_enabled("NIFTY", True, "rule", account_id=DEMO); err = None
except ValueError as exc:
    err = str(exc)
check("an Indian index on Exness -> refused", err and "BTC and GOLD" in err, err)
ex.set_enabled("BTC", True, "rule", account_id=DEMO)
check("BTC rule tickets ON for the DEMO account", ex.enabled["BTC"] and ex.account_for("BTC")["kind"] == "demo")
check("the AI desk's switch is separate - still off", not ex.enabled_ai["BTC"])
saved = json.load(open(log + ".exness_live.json"))
check("saved to disk (survives a restart) with the account, and no token", saved["acct_for"]["rule"]["BTC"] == DEMO
      and TOKEN not in json.dumps(saved))
check("...readable by the server's user only (0600)", oct(os.stat(log + ".exness_live.json").st_mode & 0o777) == "0o600")

print("4. A TICKET OPENS -> A MARKET ORDER WITH THE STOP AND TARGET AT EXNESS")
trade = {"trade_id": "BTC-20261003-210000", "index": "BTC", "option_type": "CE", "lots": 0.25, "entry_spot": 84005.0,
         "index_targets": [84300.0, 84600.0, 84900.0], "index_sl": 83700.0, "exit_at": "T2"}
ex.handle("opened", trade, "rule")
t = fake.trades[-1][1]
check("BUY 0.25 BTCUSDm (the account's own symbol name), stop 83700 and target T2 = 84600 attached",
      t["actionType"] == "ORDER_TYPE_BUY" and t["symbol"] == "BTCUSDm" and t["volume"] == 0.25
      and t["stopLoss"] == 83700.0 and t["takeProfit"] == 84600.0, t)
check("marked as the tool's own by its comment, and NO clientId (MetaApi refuses one not shaped strategy_position_order)",
      t["comment"] == "TradePicker" and "clientId" not in t)
check("sent to the DEMO account", fake.trades[-1][0] == DEMO)
pos = ex.positions[trade["trade_id"]]
check("held: Exness's own fill 84010 (the ask), the position id, the margin it needed",
      pos["state"] == "open" and pos["avg_price"] == 84010.0 and pos["position_id"] and pos["margin_needed"] == 105.01,
      {k: pos.get(k) for k in ("state", "avg_price", "margin_needed")})
pub = {"open": True, "trade_id": trade["trade_id"], "cfd": True, "tracked_on": "index", "entry": 84005.0, "pnl": -1.25}
real_entry.apply(ex, pub)
check("the page's ticket shows Exness's fill as the entry and Exness's own P&L",
      pub["entry"] == 84010.0 and pub["entry_signal"] == 84005.0 and pub["entry_venue"] == "Exness" and pub["pnl"] == -2.5, pub)
ex.handle("opened", trade, "rule")
check("the same ticket again -> no second order", sum(1 for a, b_ in fake.trades if b_["actionType"] == "ORDER_TYPE_BUY") == 1)
ex.handle("opened", dict(trade, trade_id="GOLD-1", index="GOLD"), "rule")
check("GOLD's switch is off -> nothing sent", "GOLD-1" not in ex.positions)

print("5. NOT ENOUGH MARGIN ON THE REAL ACCOUNT -> NOTHING SENT, SAID PLAINLY")
ex.set_enabled("GOLD", True, "rule", account_id=REAL)
n = len(fake.trades)
ex.handle("opened", {"trade_id": "GOLD-2", "index": "GOLD", "option_type": "PE", "lots": 0.5, "entry_spot": 4140.4,
                     "index_targets": [4130, 4120, 4110], "index_sl": 4150.0, "exit_at": "T2"}, "rule")
g = ex.positions["GOLD-2"]
check("0.5 lot gold needs ~$1,035 at 1:200, the real account has $250.75 free -> refused, no order",
      len(fake.trades) == n and g["state"] == "failed" and "free margin" in g["error"], g.get("error"))
check("...and a note says so", "NOT placed" in ex.notes[0]["text"] and ex.notes[0]["level"] == "error")
ex.handle("opened", {"trade_id": "GOLD-3", "index": "GOLD", "option_type": "PE", "lots": 0.001, "entry_spot": 4140.4,
                     "index_targets": [4130, 4120, 4110], "index_sl": 4150.0}, "rule")
check("under Exness's 0.01 minimum -> refused", ex.positions["GOLD-3"]["state"] == "failed"
      and "minimum" in ex.positions["GOLD-3"]["error"])

print("6. THE STOP TRAILS -> MOVED AT EXNESS; THE TICKET CLOSES -> CLOSED AT EXNESS")
ex.handle("trailed", dict(trade, index_sl=84300.0), "rule")
m = fake.trades[-1][1]
check("POSITION_MODIFY to 84300, target kept", m["actionType"] == "POSITION_MODIFY" and m["stopLoss"] == 84300.0
      and m["takeProfit"] == 84600.0)
ex.handle("closed", trade, "rule")
c = fake.trades[-1][1]
check("POSITION_CLOSE_ID sent", c["actionType"] == "POSITION_CLOSE_ID" and c["positionId"] == pos["position_id"])
check("banked from Exness's own closing deal: 84300, $72.50, swap -$4.75", pos["state"] == "closed"
      and pos["exit_price"] == 84300.0 and pos["gross_pnl"] == 72.5 and pos["swap"] == -4.75)
check("fills() reports it for the AI desk's record", ex.fills()[0]["exit_avg"] == 84300.0)

ex.set_enabled("BTC", False, "rule")
n = len(fake.trades)
ex.handle("opened", dict(trade, trade_id="BTC-OFF"), "rule")
check("switched OFF (its demo account still remembered) -> a new ticket sends NOTHING",
      len(fake.trades) == n and "BTC-OFF" not in ex.positions and ex.account_for("BTC")["kind"] == "demo")
ex.handle("opened", dict(trade, trade_id="BTC-AI"), "ai")
check("...and the AI desk's switch, never turned on, sends nothing either", len(fake.trades) == n)
ex.set_enabled("BTC", True, "rule", account_id=DEMO)

print("7. EXNESS'S OWN STOP OR TARGET CLOSES FIRST -> THE TOOL'S TICKET CLOSES TOO")
t2 = dict(trade, trade_id="BTC-2")
ex.handle("opened", t2, "rule")
p2 = ex.positions["BTC-2"]
fake.positions.pop(p2["position_id"])           # Exness took it out on its own
fake.close_reason = "DEAL_REASON_TP"
ex.poll()
check("seen gone -> banked, and the ticket closed as a T2 hit filled at Exness",
      p2["state"] == "closed" and closed and closed[-1][0] == "BTC" and "T2 hit" in closed[-1][1]
      and closed[-1][2] == 84300.0 and closed[-1][3] == "BTC-2", closed[-1:])
t3 = dict(trade, trade_id="BTC-3")
ex.handle("opened", t3, "rule")
fake.positions.pop(ex.positions["BTC-3"]["position_id"])
fake.close_reason = "DEAL_REASON_SL"
ex.poll()
check("...and Exness's stop-loss -> 'stop-loss hit (filled at Exness)'", "stop-loss hit" in closed[-1][1])

print("7b. A POSITION STILL OPEN AT EXNESS -> NO SECOND ONE (a restart closes the ticket, not the position)")
ex.handle("opened", dict(trade, trade_id="BTC-4"), "rule")
n = len(fake.trades)
ex.handle("opened", dict(trade, trade_id="BTC-5"), "rule")
check("BTC-4 still open on the demo account -> the next BTC ticket sends NOTHING, and says why",
      len(fake.trades) == n and "BTC-5" not in ex.positions and "BTC-4 is still open at Exness" in ex.notes[0]["text"],
      ex.notes[0]["text"])
fake.positions.pop(ex.positions["BTC-4"]["position_id"])
ex.poll()
ex.handle("opened", dict(trade, trade_id="BTC-6"), "rule")
check("...once Exness has closed it, the next one goes through", ex.positions["BTC-6"]["state"] == "open"
      and len(fake.trades) == n + 1)
fake.positions.pop(ex.positions["BTC-6"]["position_id"])
ex.poll()

print("7c. NEVER AN ORDER WITHOUT A DIRECTION, A STOP AND A TARGET (4 Oct 2026: an empty ticket went out as a SELL)")
n = len(fake.trades)
ex.handle("opened", dict(trade, trade_id="BTC-NODIR", option_type=None), "rule")
check("no direction -> nothing sent (it used to be read as SELL), and a note says why", len(fake.trades) == n
      and "BTC-NODIR" not in ex.positions and "no direction" in ex.notes[0]["text"], ex.notes[0]["text"])
ex.handle("opened", dict(trade, trade_id="BTC-NOSL", index_sl=None), "rule")
check("no stop -> nothing sent", len(fake.trades) == n and "BTC-NOSL" not in ex.positions and "stop and target" in ex.notes[0]["text"])
ex.handle("opened", dict(trade, trade_id="BTC-NOTP", index_targets=[None, None, None]), "rule")
check("no target -> nothing sent", len(fake.trades) == n and "BTC-NOTP" not in ex.positions)

print("7d. THE FORWARD-TEST GUARD: LIVE ORDERS OFF (NEVER ON) WHEN THE DEMO'S RECORD IS CLEARLY WORSE THAN THE TEST")
def guard_ex(rows):
    g = eo.Executor(EMAIL, os.path.join(tempfile.mkdtemp(), "trades.csv"),
                    client_factory=lambda acct: eo.Client(TOKEN, acct, session=fake), start=False)
    g.set_enabled("BTC", True, "rule", account_id=DEMO)
    for n_, (tid, pnl, qty, src) in enumerate(rows):
        g.positions[tid] = {"trade_id": tid, "index": "BTC", "source": src, "state": "closed", "gross_pnl": pnl, "qty": qty}
    return g
def day_rows(results, start=dt.datetime(2026, 10, 5, 9, 0), qty=0.25, src="rule"):
    return [((start + dt.timedelta(hours=2 * n)).strftime("BTC-%Y%m%d-%H%M%S"), r, qty, src) for n, r in enumerate(results)]
edge = guard_ex(day_rows([12.0] * 17 + [-70.0] * 8))                     # 25 trades, 68% won
check("17 of 25 won (68%): an 87% rule still does that 1.1% of the time - not yet proof, left ON",
      edge._guard("BTC")["tripped"] is False and edge.enabled["BTC"] is True and edge._guard("BTC")["p_low"] > 0.01)
bad = guard_ex(day_rows([12.0] * 16 + [-70.0] * 9))                      # 25 trades, 64% won
rec = bad._guard("BTC")
check("16 of 25 won (64%): an 87% rule does that under 1% of the time -> live orders switched OFF, the page told why",
      rec["tripped"] and bad.enabled["BTC"] is False and "forward-test guard" in bad.notes[0]["text"]
      and rec["p_low"] < 0.01 and bad.public()["guard"]["BTC"]["n"] == 25, (rec, bad.notes[:1]))
good = guard_ex(day_rows([12.0] * 22 + [-70.0] * 3))                     # 25 trades, 88% won
check("22 of 25 won (88%): left ON", good._guard("BTC")["tripped"] is False and good.enabled["BTC"] is True)
few = guard_ex(day_rows([12.0] * 3 + [-70.0] * 7))                       # 10 trades, 30% - but too few yet
check("only 10 trades: not judged on the win rate yet (20 needed)", few._guard("BTC")["tripped"] is False
      and few.enabled["BTC"] is True)
deep = guard_ex(day_rows([-1000.0] * 3))                                 # 3 x -$4,000 per BTC = 12,000 > 11,037
check("a drawdown past 1.5 x the test's worst ($12,000 per BTC): OFF even before 20 trades",
      deep._guard("BTC")["tripped"] and deep.enabled["BTC"] is False and "drawdown" in deep.notes[0]["text"])
old = guard_ex(day_rows([-70.0] * 30, start=dt.datetime(2026, 10, 1, 9, 0)) + day_rows([12.0] * 5))
r_old = old._guard("BTC")
check("trades from before the forward test's start (the old rules, the 4 Oct bug) are not counted",
      r_old["n"] == 5 and r_old["tripped"] is False and old.enabled["BTC"] is True, r_old)
ai = guard_ex(day_rows([-70.0] * 25, src="ai"))
check("the AI desk's positions are not the rule's record", ai._guard("BTC")["n"] == 0 and ai.enabled["BTC"] is True)
back = guard_ex(day_rows([12.0] * 25))
back.set_enabled("BTC", False, "rule")
back._guard("BTC")
check("a good record never switches them back ON - that stays the user's decision", back.enabled["BTC"] is False)
gx = guard_ex(day_rows([12.0] * 16 + [-70.0] * 9))
gx.positions["BTC-20261006-090000"] = {"trade_id": "BTC-20261006-090000", "index": "BTC", "source": "rule",
                                       "state": "open", "qty": 0.25, "position_id": "p1", "side": "BUY", "symbol": "BTCUSDm"}
gx._bank(type("C", (), {"deals": lambda self, pid: []})(), gx.positions["BTC-20261006-090000"], "test")
check("every close is checked: banking a position runs the guard", gx.enabled["BTC"] is False)

print("8. WHAT THE PAGE IS SENT - NO TOKEN, THE ACCOUNT KIND PER SWITCH")
pubx = ex.public()
check("enabled per index, venue Exness, the account kind per switch",
      pubx["venue"] == "Exness" and pubx["enabled"]["BTC"] and pubx["accounts"]["BTC"]["rule"]["kind"] == "demo"
      and pubx["accounts"]["GOLD"]["rule"]["kind"] == "real")
check("no token in anything the page gets", TOKEN not in json.dumps(pubx))
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "exness_orders.py")).read()
check("the executor only ever sends the documented trade actions",
      set(__import__("re").findall(r'"(ORDER_TYPE_\w+|POSITION_\w+)"', src)) ==
      {"ORDER_TYPE_BUY", "ORDER_TYPE_SELL", "POSITION_MODIFY", "POSITION_CLOSE_ID"})

print("8b. THE PAGE'S SWITCH (/api/live) - AN ACCOUNT MUST BE CHOSEN, REAL MONEY CONFIRMED")
import feeds
import web_server


class _Feed:
    def __init__(self, live):
        self.live = live


def live_call(form, user=EMAIL):
    saved = feeds.for_user
    feeds.for_user = lambda email, mkt=None, start=True: _Feed(ex)
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: "crypto"
    h._current_user = lambda: user
    h._same_origin = lambda: True
    try:
        h._do_live(form)
    finally:
        feeds.for_user = saved
    return json.loads(out["body"])


ex.set_enabled("BTC", False, "rule")
r = live_call({"index": "BTC", "on": "1"})
check("on without an account -> asked to choose demo or real, nothing switched", not r["ok"] and "demo or real" in r["message"]
      and not ex.enabled["BTC"], r)
r = live_call({"index": "BTC", "on": "1", "account": REAL})
check("the REAL account without its confirmation -> refused", not r["ok"] and "real money" in r["message"].lower()
      and not ex.enabled["BTC"], r)
r = live_call({"index": "BTC", "on": "1", "account": REAL, "confirm_real": "1"})
check("the REAL account WITH the confirmation -> on, and the page is told which account",
      r["ok"] and ex.enabled["BTC"] and r["live"]["accounts"]["BTC"]["rule"]["kind"] == "real", r.get("message"))
r = live_call({"index": "BTC", "on": "1", "account": DEMO})
check("the DEMO account needs no extra confirmation", r["ok"] and ex.account_for("BTC")["kind"] == "demo")
r = live_call({"index": "BTC", "on": "1", "account": "acc-someone-else"})
check("an account that is not this user's -> refused", not r["ok"])
r = live_call({"index": "NIFTY", "on": "1", "account": DEMO})
check("an Indian index on the Exness market -> refused", not r["ok"] and "Bitcoin and gold" in r["message"])
r = live_call({"index": "ALL", "on": "1", "account": DEMO})
check("ALL with the demo account: BTC and GOLD, rule and AI, all on the demo",
      r["ok"] and all(ex.switches(s)[k] for s in ("rule", "ai") for k in eo.INDICES)
      and all(ex.account_for(k, s)["kind"] == "demo" for s in ("rule", "ai") for k in eo.INDICES), r.get("message"))
r = live_call({"index": "ALL", "on": "0"})
check("ALL off needs no account", r["ok"] and not any(ex.switches(s)[k] for s in ("rule", "ai") for k in eo.INDICES))
saved_stored = ux.stored
ux.stored = lambda email: []
r = live_call({"index": "BTC", "on": "1", "account": DEMO})
ux.stored = saved_stored
check("no Exness connection at all -> sent to the Exness page", not r["ok"] and r.get("connect_url") == "/connect-exness")

print("9. REMOVING THE CONNECTION")
ux.disconnect(EMAIL)
check("token and accounts gone", ux.token_for(EMAIL) is None and ux.stored(EMAIL) == [])

print()
if fails:
    print(f"EXNESS LIVE TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("EXNESS LIVE TEST PASSED")
