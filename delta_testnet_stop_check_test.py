#!/usr/bin/env python3
"""delta_testnet_stop_check.py, run against a fake Delta - never the network.

The check exists to answer one question with Delta's own replies: does Delta
India hold a stop order on a BTC option? What is tested here is that it asks
that safely and reads the answers honestly:

  * it can only ever talk to the testnet host, and never sees this tool's keys;
  * everything it places is cancelled, also after a crash, and it sells back no
    more than it bought - a position or an order that was already on the
    account is left alone;
  * stage 1 cannot fill anything and stage 2 holds ONE contract, briefly;
  * the verdict is right in every case Delta could answer, including the one
    stage 1 cannot separate (a stop refused only because nothing is held).

The fake's error codes are invented for the test; Delta's real ones are exactly
what the check is there to find out.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import urllib.parse

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import delta_testnet_stop_check as dc
import user_delta

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


# =============================================================================
class Exchange:
    """Just enough of Delta: products, tickers, orders, positions, wallet."""

    def __init__(self, stops="accept", fills=True, auth=None, baseline_refuse=False, crash_on_order=None,
                 with_options=True, start_position=0, other_orders=(), only_nobook=False):
        self.stops, self.fills, self.auth = stops, fills, auth
        self.baseline_refuse, self.crash_on_order = baseline_refuse, crash_on_order
        self.tick = 0.5
        self.prods = {"BTCUSD": dict(id=27, mark=90000.0, bid=89995.0, ask=90005.0)}
        if with_options:
            self.prods.update({
                "C-BTC-80000-250926": dict(id=500, mark=5.0, bid=4.5, ask=5.5),          # too cheap to trade
                "C-BTC-90000-250926": dict(id=501, mark=500.0, bid=495.0, ask=505.0),     # the good one (spread 2%)
                "C-BTC-85000-250926": dict(id=502, mark=100.0, bid=None, ask=None)})      # no book, and sorts first by name
        if only_nobook:
            del self.prods["C-BTC-90000-250926"]
        self.by_id = {p["id"]: dict(p, symbol=s) for s, p in self.prods.items()}
        self.orders, self.pos, self.requests, self.order_count, self.next_id = {}, {501: 0}, [], 0, 9000
        self.max_resting = 0
        self.pos[501] = start_position
        for i, coid in enumerate(other_orders):                     # orders that were on the account already
            self.orders[7000 + i] = dict(id=7000 + i, product_id=501, state="open", client_order_id=coid, size=1, unfilled_size=1)

    def err(self, code, ctx=None, http=400):
        return http, {"success": False, "error": {"code": code, "context": ctx or {}}}

    def ok(self, result):
        return 200, {"success": True, "result": result}

    def send(self, method, path, params=None, body=None):
        self.requests.append((method, path, params, body))
        parts = path.strip("/").split("/")
        if path == "/v2/wallet/balances":
            if self.auth:
                return self.err(self.auth, {"client_ip": "203.0.113.9"}, 401)
            return self.ok([{"asset_symbol": "USD", "available_balance": "10000"}])
        if parts[:2] == ["v2", "products"] and len(parts) == 3:
            p = self.prods.get(parts[2])
            return self.ok({"id": p["id"], "tick_size": str(self.tick), "contract_value": "0.001",
                            "settlement_time": "2026-09-26T12:00:00Z"}) if p else self.err("product_not_found", http=404)
        if path == "/v2/tickers":
            rows = [{"symbol": s, "mark_price": str(p["mark"]),
                     "quotes": {"best_bid": p["bid"], "best_ask": p["ask"]}} for s, p in self.prods.items() if s[:2] == "C-"]
            return self.ok(rows)
        if parts[:2] == ["v2", "tickers"] and len(parts) == 3:
            p = self.prods[parts[2]]
            return self.ok({"symbol": parts[2], "mark_price": str(p["mark"]),
                            "quotes": {"best_bid": None if p["bid"] is None else str(p["bid"]),
                                       "best_ask": None if p["ask"] is None else str(p["ask"])}})
        if path == "/v2/positions":
            pid = int((params or {}).get("product_id"))
            return self.ok([{"product_id": pid, "size": self.pos.get(pid, 0)}])
        if method == "GET" and path == "/v2/orders":
            ids = {int(x) for x in str((params or {}).get("product_ids", "")).split(",") if x}
            return self.ok([o for o in self.orders.values() if o["state"] in ("open", "pending") and o["product_id"] in ids])
        if method == "GET" and parts[:2] == ["v2", "orders"]:
            o = self.orders.get(int(parts[2]))
            return self.ok(o) if o else self.err("open_order_not_found", http=404)
        if method == "DELETE" and path == "/v2/orders":
            o = self.orders.get(int(body["id"]))
            if o and o["state"] in ("open", "pending"):
                o["state"] = "cancelled"
            return self.ok({"id": body["id"]})
        if method == "POST" and path == "/v2/orders":
            return self.place(body)
        raise AssertionError(f"the check made a request the fake does not know: {method} {path}")

    def place(self, b):
        if self.crash_on_order is not None and self.order_count == self.crash_on_order:
            self.crash_on_order = None                   # once: the cleanup that follows must be able to place its sell
            raise RuntimeError("boom")
        self.order_count += 1
        pid = b["product_id"]
        prod = self.by_id[pid]
        opt = prod["symbol"][:2] in ("C-", "P-")
        self.next_id += 1
        o = dict(b, id=self.next_id, state="open", unfilled_size=b["size"])
        if b.get("stop_order_type"):
            if opt and self.stops == "refuse_always":
                return self.err("stop_orders_not_supported_for_options")
            if opt and self.stops == "refuse_without_position" and self.pos.get(pid, 0) <= 0:
                return self.err("no_position_for_reduce_only" if b.get("reduce_only") else "insufficient_margin")
            o["state"] = "pending"
        elif b["order_type"] == "limit_order" and b["side"] == "buy":
            if self.baseline_refuse and b.get("post_only"):
                return self.err("price_band_breached")
            if self.fills and float(b["limit_price"]) >= prod["ask"]:
                self.pos[pid] = self.pos.get(pid, 0) + b["size"]
                o.update(state="closed", unfilled_size=0)
        elif b["order_type"] == "market_order" and b["side"] == "sell" and b.get("reduce_only"):
            take = min(b["size"], max(self.pos.get(pid, 0), 0))
            self.pos[pid] = self.pos.get(pid, 0) - take
            o.update(state="closed", unfilled_size=b["size"] - take)
        self.orders[o["id"]] = o
        self.max_resting = max(self.max_resting, len(self.resting()))
        return self.ok({"id": o["id"], "state": o["state"]})

    def resting(self, prefix=dc.PREFIX):
        return [o for o in self.orders.values() if o["state"] in ("open", "pending")
                and str(o.get("client_order_id") or "").startswith(prefix)]


def go(ex, **kw):
    out = io.StringIO()
    api = dc.Api(ex.send)
    with contextlib.redirect_stdout(out):
        res = dc.run(api, out=print, **kw)
    return res, out.getvalue()


# -----------------------------------------------------------------------------
print("1. IT CAN ONLY EVER REACH THE TESTNET")
for host in ("https://api.india.delta.exchange", "https://api.delta.exchange", "https://example.com",
             "https://cdn-ind.testnet.deltaex.org.evil.example", "http://localhost:5055"):
    try:
        dc.Api(lambda *a, **k: (200, {}), base=host)
        refused = False
    except dc.NotTestnet:
        refused = True
    check(f"the API wrapper refuses {host}", refused)
check("...and accepts Delta India's testnet", dc.Api(lambda *a, **k: (200, {})).base == user_delta.TESTNET)
calls = []
api_late = dc.Api(lambda *a, **k: calls.append(a) or (200, {}))
api_late.base = "https://api.india.delta.exchange"                  # changed after it was built
try:
    api_late.call("GET", "/v2/wallet/balances")
    late = False
except dc.NotTestnet:
    late = True
check("the host is checked again on every request, so a base changed later cannot get through", late and not calls)
sent = []
import requests
real_request = requests.request
requests.request = lambda *a, **k: sent.append((a, k)) or (_ for _ in ()).throw(AssertionError("network touched"))
try:
    tx = dc.requests_transport("KEY", "SECRET", base="https://api.india.delta.exchange")
    try:
        tx("GET", "/v2/wallet/balances")
        blocked = False
    except dc.NotTestnet:
        blocked = True
    check("the real transport refuses the production host before any request leaves", blocked and not sent)
finally:
    requests.request = real_request

print("1b. THE REAL TRANSPORT: THE TOOL'S OWN SIGNING, ONE HOST, NO SECRET ON THE WIRE")
wire = []
class Resp:
    def __init__(self, st, body):
        self.status_code, self._b = st, body
    def json(self):
        return self._b
def fake_request(method, url, headers=None, data=None, timeout=None):
    u = urllib.parse.urlparse(url)
    wire.append((method, url, dict(headers or {}), data))
    params = dict(urllib.parse.parse_qsl(u.query)) or None
    body = json.loads(data) if data else None
    return Resp(*EX_WIRE.send(method, u.path, params, body))
out = io.StringIO()
requests.request = fake_request
try:
    EX_WIRE = Exchange()
    api_w = dc.Api(dc.requests_transport("KEY123", "SECRET456"))
    with contextlib.redirect_stdout(out):
        res_w = dc.run(api_w)
finally:
    requests.request = real_request
check("a whole stage 1 ran over the real transport", len(wire) > 10 and res_w["attempts"], len(wire))
check("every request went to the testnet host and nowhere else",
      all(urllib.parse.urlparse(u).hostname == dc.TESTNET_HOST for _m, u, _h, _d in wire))
h0 = wire[0][2]
ts = h0["timestamp"]
check("signed exactly as the tool signs (api-key, timestamp and an HMAC over method+timestamp+path)",
      h0["api-key"] == "KEY123" and h0["signature"] == user_delta.sign("SECRET456", "GET", ts, "/v2/wallet/balances"))
blob = json.dumps(res_w["log"], default=str) + out.getvalue() + json.dumps(res_w["attempts"], default=str)
check("the secret is nowhere in the transcript, the attempts or the printed output; the key is not either",
      "SECRET456" not in blob and "KEY123" not in blob)
check("the secret is not in any URL or body that was sent", all("SECRET456" not in u and "SECRET456" not in (d or "") for _m, u, _h, d in wire))

print("2. STAGE 1 - NOTHING CAN FILL, EVERYTHING IS CANCELLED")
ex = Exchange(stops="accept")
res, text = go(ex)
buys = [b for m, p, q, b in ex.requests if m == "POST" and b and b.get("side") == "buy"]
check("the only buy placed is the far-below baseline, post-only, below the best bid",
      len(buys) == 1 and buys[0]["post_only"] is True and float(buys[0]["limit_price"]) < 495.0, buys)
check("each order is cancelled the moment it is accepted: never more than one of its own resting at a time",
      ex.max_resting <= 1, ex.max_resting)
check("nothing was held at any point", ex.pos == {501: 0} and not any(o["state"] == "closed" for o in ex.orders.values()))
check("every order it placed was cancelled - none resting", ex.resting() == [] and len(ex.orders) >= 5)
check("all its orders carry the TPCHK id", all(str(o["client_order_id"]).startswith("TPCHK") for o in ex.orders.values()))
check("it traded only the chosen option and the perpetual, nothing else",
      {b["product_id"] for m, p, q, b in ex.requests if m == "POST" and b} <= {501, 27})
check("it picked the option with a real book, not the too-cheap one or the one with no book (which sorts first by name)",
      {b["product_id"] for m, p, q, b in ex.requests if m == "POST" and b and b["product_id"] != 27} == {501})
opt_stops = [a for a in res["attempts"] if a["name"].startswith("option:")]
check("three stop shapes were tried on the option: market, reduce-only market, reduce-only limit", len(opt_stops) == 3)
check("stops are sells below the market, triggered on the mark price",
      all(a["sent"]["side"] == "sell" and float(a["sent"]["stop_price"]) < 500.0 and a["sent"]["stop_trigger_method"] == "mark_price"
          for a in opt_stops))
check("prices are sent as strings at the tick's precision", all(isinstance(a["sent"]["stop_price"], str) and a["sent"]["stop_price"] == f"{float(a['sent']['stop_price']):.1f}" for a in opt_stops))
check("a control on the perpetual with the same kind of request", any(a["name"].startswith("CONTROL") and a["accepted"] for a in res["attempts"]))
check("if Delta accepts them all, the verdict is promising - and asks for stage 2", res["verdict"].startswith("PROMISING") and "--with-position" in res["verdict"], res["verdict"][:60])

print("3. STAGE 1 WHEN DELTA REFUSES")
ex = Exchange(stops="refuse_always")
res, text = go(ex)
check("refused everywhere on the option, accepted on the perpetual: not decided, and it says the request was well formed",
      res["verdict"].startswith("NOT YET DECIDED") and "well formed" in res["verdict"] and "stop_orders_not_supported_for_options" in res["verdict"], res["verdict"])
check("Delta's own error code is printed as Delta said it", "stop_orders_not_supported_for_options" in text)
check("nothing left resting", ex.resting() == [])
ex = Exchange(stops="accept", baseline_refuse=True)
res, _ = go(ex)
check("if even the plain far-away buy is refused, the verdict says the account cannot take orders - not that stops are unsupported",
      res["verdict"].startswith("INCONCLUSIVE"), res["verdict"][:70])
check("it tried the buy at a second price before giving up on it", sum(1 for m, p, q, b in ex.requests if m == "POST" and b and b.get("post_only")) == 2)

print("4. STAGE 2 - ONE CONTRACT HELD, A REDUCE-ONLY STOP ASKED FOR")
ex = Exchange(stops="accept")
res, text = go(ex, with_position=True)
held = [a for a in res["attempts"] if a["name"].startswith("option: HELD")]
check("it bought exactly one contract", ex.orders and [b for m, p, q, b in ex.requests if m == "POST" and b and b.get("side") == "buy" and "stop_order_type" not in b and not b.get("post_only")][0]["size"] == 1)
check("the buy went in a little above the ask so it fills", float([b for m, p, q, b in ex.requests if m == "POST" and b and b.get("side") == "buy" and not b.get("post_only")][0]["limit_price"]) >= 505.0)
check("two reduce-only stops were asked for on the HELD position", len(held) == 2 and all(a["sent"]["reduce_only"] is True for a in held))
check("Delta's own record of the order was read back - it RESTS at the exchange", held[0].get("read_back", {}).get("state") == "pending"
      and held[0]["read_back"]["reduce_only"] is True and held[0]["read_back"]["stop_order_type"] == "stop_loss_order", held[0].get("read_back"))
check("the verdict is YES, with what Delta showed", res["verdict"].startswith("YES") and "pending" in res["verdict"], res["verdict"][:90])
check("everything resting was cancelled", ex.resting() == [])
check("the contract it bought was sold back with a reduce-only market order: net position zero", ex.pos[501] == 0)
sells = [b for m, p, q, b in ex.requests if m == "POST" and b and b.get("side") == "sell" and b["order_type"] == "market_order" and "stop_order_type" not in b]
check("...for exactly what it bought", len(sells) == 1 and sells[0]["size"] == 1 and sells[0]["reduce_only"] is True, sells)

print("4b. THE CASE STAGE 1 CANNOT TELL APART - REFUSED ONLY BECAUSE NOTHING WAS HELD")
ex = Exchange(stops="refuse_without_position")
res1, _ = go(ex)
check("stage 1 alone is refused on every option stop, for reasons that are about holding nothing",
      res1["verdict"].startswith("NOT YET DECIDED") and any(a["code"] == "no_position_for_reduce_only" for a in res1["attempts"]), res1["verdict"][:60])
ex = Exchange(stops="refuse_without_position")
res2, _ = go(ex, with_position=True)
check("stage 2 finds it: with a position held, Delta takes the stop - YES", res2["verdict"].startswith("YES"), res2["verdict"][:70])
check("...and leaves the account flat", ex.pos[501] == 0 and ex.resting() == [])

print("4c. WHEN DELTA REFUSES EVEN A HELD OPTION")
ex = Exchange(stops="refuse_always")
res, _ = go(ex, with_position=True)
check("the verdict is NO, with Delta's codes, and says the tool's own watch of the mark is then the only stop",
      res["verdict"].startswith("NO ") and "stop_orders_not_supported_for_options" in res["verdict"] and "own watch of the mark is the only stop" in res["verdict"], res["verdict"][:80])
check("the contract was still sold back", ex.pos[501] == 0 and ex.resting() == [])

print("4d. A BUY THAT DOES NOT FILL")
ex = Exchange(stops="accept", fills=False)
res, text = go(ex, with_position=True)
check("the unfilled buy is cancelled, nothing is held, no stop is asked for on a position that does not exist",
      ex.pos[501] == 0 and ex.resting() == [] and not [a for a in res["attempts"] if a["name"].startswith("option: HELD")])
check("and it says so rather than pretending", "did not fill" in text)
buy_id = next(o["id"] for o in ex.orders.values() if o["side"] == "buy" and not o.get("post_only"))
i_cancel = next(i for i, (m, pth, q, b) in enumerate(ex.requests) if m == "DELETE" and b and b.get("id") == buy_id)
i_sweep = next(i for i, (m, pth, q, b) in enumerate(ex.requests) if m == "GET" and pth == "/v2/orders" and q and "states" in q)
check("the unfilled buy is cancelled straight away by the check itself, not left for the final sweep", i_cancel < i_sweep, (i_cancel, i_sweep))

print("5. WHAT WAS ALREADY ON THE ACCOUNT IS LEFT ALONE")
ex = Exchange(stops="accept", start_position=3, other_orders=("my-own-order", "manual"))
res, _ = go(ex, with_position=True)
check("a position that was already there is not sold: it was 3, it bought 1, it sold 1 - 3 remain", ex.pos[501] == 3, ex.pos)
check("orders that are not TPCHK's are never cancelled", sorted(o["client_order_id"] for o in ex.resting(prefix="")) == ["manual", "my-own-order"],
      [o["client_order_id"] for o in ex.resting(prefix="")])

print("6. A CRASH MID-RUN STILL CLEANS UP")
# Stage 1 places five orders (counts 0-4), stage 2's buy is count 5, its first HELD stop is count 6.
ex2 = Exchange(stops="accept", crash_on_order=6)                    # crash on the first stop, after the contract was bought
for e in (ex2,):
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            dc.run(dc.Api(e.send), out=print, with_position=True)
        crashed = False
    except RuntimeError:
        crashed = True
    check("the crash was not swallowed", crashed)
    check("the contract already bought is sold back", e.pos[501] == 0, e.pos)
    check("nothing of theirs is left resting", e.resting() == [])
ex3 = Exchange(stops="accept", crash_on_order=2)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        dc.run(dc.Api(ex3.send), out=print)
except RuntimeError:
    pass
check("a crash in stage 1 leaves nothing resting either", ex3.resting() == [] and ex3.pos[501] == 0)

print("7. WHEN IT CANNOT EVEN SIGN IN")
ex = Exchange(auth="ip_not_whitelisted_for_api_key")
res, text = go(ex)
check("an unregistered IP is named, with the address Delta saw", "203.0.113.9" in text and "whitelisted" in text and res["verdict"].startswith("NO ANSWER"), text[:200])
check("no order was placed", not [r for r in ex.requests if r[0] == "POST"])
ex = Exchange(auth="invalid_api_key")
res, text = go(ex)
check("a key Delta does not know points at the Demo account (a production key does not work on the testnet)",
      "Demo account" in text and "testnet.delta.exchange" in text)
ex = Exchange(only_nobook=True)
res, text = go(ex, with_position=True)
check("stage 2 is never run on an option with no book (there would be no ask to buy at)",
      res["verdict"].startswith("NO ANSWER") and not [r for r in ex.requests if r[0] == "POST"])
ex = Exchange(with_options=False)
res, text = go(ex)
check("no usable option on the testnet: no answer, and nothing ordered", res["verdict"].startswith("NO ANSWER") and not [r for r in ex.requests if r[0] == "POST"])

print("8. THE COMMAND LINE")
old_env = {k: os.environ.pop(k, None) for k in ("DELTA_TESTNET_KEY", "DELTA_TESTNET_SECRET")}
called = []
requests.request = lambda *a, **k: called.append(a) or (_ for _ in ()).throw(AssertionError("network touched"))
try:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = dc.main([])
    check("without the two environment variables it stops, says what to set, and touches nothing",
          rc == 2 and "DELTA_TESTNET_KEY" in out.getvalue() and not called)
    check("it says it never reads this tool's own keys", "never reads this tool's own Delta keys" in out.getvalue())
finally:
    requests.request = real_request
    for k, v in old_env.items():
        if v is not None:
            os.environ[k] = v
SRC = open(os.path.join(HERE, "delta_testnet_stop_check.py")).read()
check("the script never names the production host, the tool's saved keys or its accounts",
      "api.india.delta.exchange" not in SRC and "keys_for" not in SRC and "import accounts" not in SRC and "user_delta.BASE" not in SRC)
check("it offers no way to choose another host (no --base / --url)", "--base" not in SRC and "--url" not in SRC and "--host" not in SRC)

print("DELTA TESTNET STOP CHECK TEST PASSED" if not fails else f"DELTA TESTNET STOP CHECK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
