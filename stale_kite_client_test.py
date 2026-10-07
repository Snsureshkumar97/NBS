#!/usr/bin/env python3
"""live_orders.Executor.kite(): a new Zerodha client when the account's login changes, not only when the day does.

6 and 7 Oct 2026: the client was built before the morning's Zerodha login and kept for the day, so the day's first
entry read the funds and the quote with a dead token ("Incorrect `api_key` or `access_token`"). The order itself went
through only because the failed funds read happened to reset the client first - a first call that was a SELL or a
stop change would have failed. Runs the real _kite_factory against a stand-in Zerodha that refuses any token but the
current one."""
import datetime as dt
import os
import sys
import tempfile
import types

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


ZERODHA = {"valid": "tok-new"}          # the only token Zerodha accepts right now
BUILT = []


class TokenException(Exception):
    pass


class FakeKite:
    def __init__(self, api_key, proxies=None):
        self.api_key, self.proxies, self.token = api_key, proxies, None
        BUILT.append(self)

    def set_access_token(self, token):
        self.token = token

    def _auth(self):
        if self.token != ZERODHA["valid"]:
            raise TokenException("Incorrect `api_key` or `access_token`.")

    def margins(self, segment):
        self._auth()
        return {"net": 50000.0, "available": {"live_balance": 50000.0}}

    def quote(self, keys):
        self._auth()
        return {k: {"last_price": 151.35, "depth": {"buy": [{"price": 151.0}], "sell": [{"price": 151.4}]}} for k in keys}


sys.modules["kiteconnect"] = types.SimpleNamespace(KiteConnect=FakeKite)

import live_orders as lo
import user_kite

ACCOUNT = {"token": "tok-old", "key": "server-key", "proxy": None}
user_kite.token_for = lambda email: ACCOUNT["token"]
user_kite.api_key_for = lambda email: ACCOUNT["key"]
user_kite.order_proxy_for = lambda email: ACCOUNT["proxy"]

CLOCK = {"now": dt.datetime(2026, 10, 7, 8, 48, 30)}
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
ex = lo.Executor("me@example.invalid", path, now=lambda: CLOCK["now"], start=False)
POS = {"exchange": "NFO", "tradingsymbol": "NIFTY26O1322600PE"}

print("1. THE MORNING OF 7 OCT")
first = ex.kite()                                       # 08:48 - live switched on before the Zerodha login
check("a client built before the login carries the old token", first.token == "tok-old")
ACCOUNT["token"] = "tok-new"                            # the user logs in to Zerodha
CLOCK["now"] = dt.datetime(2026, 10, 7, 9, 22, 0)
try:
    avail = ex._available(fresh=True)
except Exception as exc:
    avail = f"raised {type(exc).__name__}: {exc}"
check("after the login, the funds read works (a new client with the new token)", avail == 50000.0, avail)
check("...and the quote gives the bid/ask", ex._quote(POS) == {"bid": 151.0, "ask": 151.4, "ltp": 151.35}, ex._quote(POS))
check("...on a client built with the new token", ex.kite().token == "tok-new" and ex.kite() is not first)

print("2. NO NEEDLESS REBUILDS")
n = len(BUILT)
same = [ex.kite() for _ in range(20)]
check("the same login: the same client, call after call", len(BUILT) == n and all(c is same[0] for c in same),
      f"built {len(BUILT) - n}")

print("3. EVERYTHING A CLIENT IS BUILT FROM")
ACCOUNT["key"] = "own-app-key"                          # the account saves its own Kite Connect app
check("a new app key: a new client with it", ex.kite().api_key == "own-app-key")
ACCOUNT["proxy"] = "http://proxy.example.invalid:8888"             # ...and its order route
check("a new order proxy: a new client through it", ex.kite().proxies == {"https": "http://proxy.example.invalid:8888"})
CLOCK["now"] = dt.datetime(2026, 10, 8, 9, 0, 0)
n = len(BUILT)
ex.kite()
check("a new day: still a new client", len(BUILT) == n + 1)

print("4. WHEN THE LOGIN GOES")
ACCOUNT["token"] = None
try:
    ex.kite()
    gone = "no error"
except RuntimeError as exc:
    gone = str(exc)
check("the token removed: no client with the old one - 'not connected'", "not connected" in gone, gone)
ACCOUNT["token"] = "tok-new"
good = ex.kite()
user_kite.token_for = lambda email: (_ for _ in ()).throw(OSError("users file busy"))
check("the login cannot be read for a moment: the client in hand is kept, the call not failed", ex.kite() is good)
user_kite.token_for = lambda email: ACCOUNT["token"]

print("5. TESTS THAT PASS THEIR OWN CLIENT")
fk = FakeKite("x")
ex2 = lo.Executor("me@example.invalid", os.path.join(tempfile.mkdtemp(), "t.csv"), kite=lambda: fk, start=False)
check("a factory without an identity: built once, kept (as before)", ex2.kite() is fk and ex2.kite() is fk)

print()
print("STALE KITE CLIENT TEST PASSED" if not fails else f"STALE KITE CLIENT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
