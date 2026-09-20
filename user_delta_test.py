#!/usr/bin/env python3
"""Each account's Delta Exchange India keys: the signature, keeping and
checking the keys, what a page may see, and what disconnecting clears.
A fake HTTP session only - nothing here reaches Delta."""
import hashlib
import hmac
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real user file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts
import user_delta as ud

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


class Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body
    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHTTP:
    """Answers like Delta: 200 + success for the right key/secret, Delta's
    own error codes otherwise. Records every request it sees."""
    def __init__(self, key="k1", secret="s1", down=False):
        self.key, self.secret, self.down = key, secret, down
        self.calls = []
    def request(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append((method, url, dict(headers or {}), data))
        if self.down:
            raise ConnectionError("no route")
        h = headers or {}
        if h.get("api-key") != self.key:
            return Resp(401, {"success": False, "error": {"code": "invalid_api_key"}})
        path = url.split("delta.exchange", 1)[-1].split("deltaex.org", 1)[-1]
        p, _, q = path.partition("?")
        want = ud.sign(self.secret, method, h.get("timestamp"), p, ("?" + q) if q else "", data or "")
        if h.get("signature") != want:
            return Resp(401, {"success": False, "error": {"code": "invalid_signature"}})
        if getattr(self, "ip_block", None):
            return Resp(403, {"success": False, "error": {"code": "ip_not_whitelisted_for_api_key",
                                                          "context": {"client_ip": self.ip_block}}})
        if "/v2/wallet/balances" in url:
            return Resp(200, {"success": True, "result": [{"asset_symbol": "USD", "available_balance": "123.45", "balance": "150.0"},
                                                          {"asset_symbol": "BTC", "available_balance": "0", "balance": "0"}]})
        return Resp(200, {"success": True, "result": {"id": 4242, "email": "someone@example.invalid"}})


print("1. THE SIGNATURE")
sig = ud.sign("secret", "GET", "1700000000", "/v2/profile", "", "")
want = hmac.new(b"secret", b"GET1700000000/v2/profile", hashlib.sha256).hexdigest()
check("HMAC-SHA256 hex of method + timestamp + path (+ query + body)", sig == want)
sig2 = ud.sign("secret", "POST", "1700000000", "/v2/orders", "?a=1", '{"size":1}')
check("the query keeps its leading ? and the body is the exact text sent",
      sig2 == hmac.new(b"secret", b'POST1700000000/v2/orders?a=1{"size":1}', hashlib.sha256).hexdigest())
h = ud.headers("k", "secret", "GET", "/v2/profile", now=1700000000)
check("headers: api-key, timestamp in whole seconds, signature", h["api-key"] == "k" and h["timestamp"] == "1700000000"
      and h["signature"] == sig and "secret" not in json.dumps(h))

print("2. CONNECTING KEEPS THE KEYS ONLY AFTER DELTA ACCEPTS THEM")
accounts.create_user("me@example.invalid", "a-long-enough-password-123")
fh = FakeHTTP()
ok, msg = ud.connect("me@example.invalid", "k1", "s1", session=fh)
check("accepted: stored, with the Delta user id", ok and ud.keys_for("me@example.invalid") == ("k1", "s1")
      and "4242" in msg, msg)
check("the check was a signed GET /v2/profile", fh.calls and fh.calls[0][0] == "GET" and "/v2/profile" in fh.calls[0][1])
ok, msg = ud.connect("me@example.invalid", "k1", "wrong", session=FakeHTTP())
check("a wrong secret is refused in Delta's words, and the old keys stay", not ok and "secret does not match" in msg
      and ud.keys_for("me@example.invalid") == ("k1", "s1"), msg)
ok, msg = ud.connect("me@example.invalid", "nope", "s1", session=FakeHTTP())
check("an unknown key is refused", not ok and "does not recognise" in msg, msg)
fb = FakeHTTP(); fb.ip_block = "203.0.113.9"
ok, msg = ud.connect("me@example.invalid", "k1", "s1", session=fb)
check("an unwhitelisted IP is named, with the IP Delta saw", not ok and "203.0.113.9" in msg and "whitelist" in msg, msg)
ok, msg = ud.connect("me@example.invalid", "k1", "s1", session=FakeHTTP(down=True))
check("Delta unreachable: refused without a traceback", not ok and "Could not reach" in msg, msg)
ok, msg = ud.connect("me@example.invalid", "", "s1")
check("blank fields are refused before any call", not ok)
ok, msg = ud.connect("me@example.invalid", "k 1", "s1")
check("whitespace inside a key is refused before any call", not ok)

print("3. STATUS AND WHAT A PAGE SEES")
ud._checks.clear()
st, detail = ud.status("me@example.invalid", session=FakeHTTP())
check("good keys: ok", st == "ok", (st, detail))
fh2 = FakeHTTP()
ud.status("me@example.invalid", session=fh2)
check("checked at most once every five minutes", fh2.calls == [])
ud._checks.clear()
st, detail = ud.status("me@example.invalid", session=FakeHTTP(key="rotated"))
check("keys Delta no longer accepts: invalid, in its words", st == "invalid" and "recognise" in detail, detail)
s = ud.summary("me@example.invalid")
check("the summary names the user id and the time, never the key or secret",
      s["user_id"] == "4242" and s["since"] and "k1" not in json.dumps(s) and "s1" not in json.dumps(s), s)
check("a fresh account: missing, and told the keys are only for live orders",
      ud.status("nobody@example.invalid")[0] == "missing" and "live orders" in ud.status("nobody@example.invalid")[1])

print("3b. THE WALLET, FOR THE USER'S OWN PAGES ONLY")
ud._checks.clear(); ud._wallets.clear()
w = ud.wallet("me@example.invalid", session=FakeHTTP())
check("balances read and rounded: asset, available, balance", w == [{"asset": "USD", "available": 123.45, "balance": 150.0},
                                                                   {"asset": "BTC", "available": 0.0, "balance": 0.0}], w)
fw = FakeHTTP(); ud.wallet("me@example.invalid", session=fw)
check("read at most once a minute", fw.calls == [])
check("nobody: no wallet", ud.wallet("nobody@example.invalid") is None)
ud._checks.clear()
ud.status("me@example.invalid", session=FakeHTTP())        # prime the cache with the fake - summary() takes no session
s = ud.summary("me@example.invalid")
check("the summary carries the wallet when the keys are accepted", s["connected"] and s["wallet"][0]["available"] == 123.45, s.get("wallet"))
html = __import__("nbs_site").delta_connect_page("me@example.invalid", "ok", "ok", user_id="1", since="x", wallet=s["wallet"])
check("the Delta page shows the wallet", "123.45 available" in html and "150.00 balance" in html)
WS0 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("the venue chip and the AI tab show what is available, from the broker block's funds",
      '"funds": funds}' in WS0 and "br.funds.available" in WS0 and "wallet: ${brk.funds.asset" in WS0)

print("4. DISCONNECTING CLEARS EVERYTHING")
ud.disconnect("me@example.invalid")
u = accounts.get_user("me@example.invalid") or {}
check("no key, secret, id or time left in the user file", ud.keys_for("me@example.invalid") is None
      and not any(u.get(k) for k in ud.FIELDS))
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_delta.py")).read()
check("nothing in the module prints or logs", "print(" not in SRC and "logging" not in SRC)

print("5. THE PAGES: EACH MARKET NAMES ITS OWN VENUE")
import web_server
import nbs_site
WS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
NS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nbs_site.py")).read()
check("a /connect-delta page, GET and POST", WS.count('path == "/connect-delta"') == 2 and "def delta_connect_page" in NS)
check("the state payload names the market's venue, and only a Zerodha market needs Zerodha",
      '"broker": self._broker(user, market)' in WS and '(not kite["connected"]) and mprov == "kite"' in WS)
check("the header chip follows the venue, never Zerodha over a crypto page",
      '$("kite").href = br.connect_url' in WS and '"Connect " + br.name' in WS)
check("the security page lists the Delta keys", "Delta Exchange API key and secret" in NS)
check("the venue chip is actually shown on a non-Zerodha market (it used to be display:none with nothing revealing it)",
      '$("kite").style.display = (needs || br.name !== "Zerodha") ? "" : "none";' in WS)
check("the Bitcoin AI tab says where to add the keys while none are added", "/connect-delta) before switching live orders on" in WS)
check("the Zerodha page walks through live orders, and no longer claims no order is ever placed",
      "developers.kite.trade" in NS and "No order is ever placed" not in NS and "step by step" in NS)
html = nbs_site.delta_connect_page("me@example.invalid", "missing", "No keys yet.")
check("the Delta page: a form for the key and the secret, the secret never echoed",
      'name="api_key"' in html and 'type="password"' in html and 'name="api_secret"' in html and "Save the keys" in html)
html2 = nbs_site.delta_connect_page("me@example.invalid", "ok", "Keys accepted.", user_id="4242", since="2026-09-20 09:00")
check("with keys: replace or remove, and who they belong to", "Replace the keys" in html2 and "Remove the keys" in html2
      and "4242" in html2)


def handler(user="me@example.invalid", same_origin=True):
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._redirect = lambda where: out.update(redirect=where)
    h._current_user = lambda: user
    h._current_market = lambda: "crypto"
    h._same_origin = lambda: same_origin
    return h, out


seen = []
orig_connect, orig_disc = ud.connect, ud.disconnect
ud.connect = lambda email, key, secret, **kw: (seen.append((email, key, secret)) or (True, "Connected to Delta Exchange India as user 7."))
ud.disconnect = lambda email: seen.append(("disconnect", email))
try:
    h, out = handler()
    h._do_connect_delta({"action": "save", "api_key": "k9", "api_secret": "s9"})
    check("saving hands the typed keys to user_delta and shows its answer", seen[-1] == ("me@example.invalid", "k9", "s9")
          and "Connected to Delta" in out["body"] and "s9" not in out["body"])
    h, out = handler(same_origin=False)
    h._do_connect_delta({"action": "save", "api_key": "k9", "api_secret": "s9"})
    check("a cross-site post is refused", "Refused" in out["body"] and len(seen) == 1)
    h, out = handler()
    h._do_connect_delta({"action": "disconnect"})
    check("removing goes through user_delta.disconnect", seen[-1] == ("disconnect", "me@example.invalid") and "removed" in out["body"])
    h, out = handler(user=None)
    h._do_connect_delta({"action": "save"})
    check("signed out: sent to the login page", out.get("redirect") == "/login")
    web_server._state["mode"] = "kite"
    b = h._broker("me@example.invalid", "crypto")
    check("the crypto market's venue is Delta Exchange, needed for live orders only",
          b["name"] == "Delta Exchange" and b["connect_url"] == "/connect-delta" and b["needed_for"] == "live orders only")
    b = h._broker("me@example.invalid", "nse_index")
    check("the Indian market's venue is Zerodha", b["name"] == "Zerodha" and b["connect_url"] == "/connect")
finally:
    ud.connect, ud.disconnect = orig_connect, orig_disc

print("USER DELTA TEST PASSED" if not fails else f"USER DELTA TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
