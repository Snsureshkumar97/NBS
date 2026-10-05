#!/usr/bin/env python3
"""An account's OWN Kite Connect app (user_kite.app_for): Zerodha lets an app be used only by the client ID
that created it (and immediate family added to it), so a friend logging in through the server's app was told
"user is not enabled for the app" (the user, 5 Oct 2026 - Vishnu bought his own app). Saving, checking and
removing it; every per-account Zerodha call using it; the server's own app untouched for everyone else.
Stubs only - nothing here reaches Zerodha."""
import os
import sys
import tempfile
import types

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real user file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts
import config
import kite_auth
import user_kite as uk

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


config.KITE_API_KEY, config.KITE_API_SECRET = "serverkey1234", "serversecret0123456789abcd"
config.WEB_PUBLIC_URL = "https://tool.example.invalid"
OWNER, FRIEND = "owner@example.invalid", "friend@example.invalid"
for e in (OWNER, FRIEND):
    accounts.create_user(e, "a-long-enough-password-123")
FKEY, FSECRET = "friendkey9xyz", "friendsecretABCDEF0123456789"

print("1. WITHOUT ITS OWN APP AN ACCOUNT USES THE SERVER'S - EXACTLY AS BEFORE")
check("the owner: the server's key and secret, not its own", uk.app_for(OWNER) == ("serverkey1234", "serversecret0123456789abcd", False))
check("no account at all (shared mode): the server's", uk.app_for(None)[0] == "serverkey1234")

print("2. SAVING AN APP - ONLY WHAT LOOKS LIKE ONE, NEVER THE SERVER'S")
for key, secret, why in (("", FSECRET, "Both"), (FKEY, "", "Both"), ("friend key", FSECRET, "letters and numbers"),
                         (FKEY, "short", "does not look like"), ("serverkey1234", FSECRET, "server's own app")):
    ok, msg = uk.save_app(FRIEND, key, secret)
    check(f"refused: {why}", not ok and why in msg and FSECRET not in msg, msg)
check("...and nothing was kept", uk.app_for(FRIEND)[2] is False)
accounts.update_user(FRIEND, {"kite_token": "old-token", "kite_user_id": "AB1234", "kite_connected": "2026-10-05"})
ok, msg = uk.save_app(FRIEND, "  " + FKEY + " ", FSECRET + "\n")
check("a real-looking key and secret are kept (spaces trimmed)", ok and uk.app_for(FRIEND) == (FKEY, FSECRET, True), msg)
check("...the old app's token is dropped - it would be refused under the new app",
      uk.token_for(FRIEND) is None and (accounts.get_user(FRIEND) or {}).get("kite_user_id") is None)
s = uk.summary(FRIEND)
check("the page learns 'own app' and the key's last 4 - never the key or the secret",
      s["own_app"] is True and s["app_key_tail"] == "9xyz" and FKEY not in str(s) and FSECRET not in str(s), s)
check("the owner's summary: not an own app", uk.summary(OWNER)["own_app"] is False and uk.summary(OWNER)["app_key_tail"] == "")

print("3. EVERY ZERODHA CALL FOR THE ACCOUNT USES ITS OWN APP")
used = []
class FakeKite:
    def __init__(self, api_key, access=None):
        self.api_key, self.access = api_key, access
        used.append(("kite", api_key, access))
    def login_url(self):
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={self.api_key}"
    def profile(self):
        return {"user_id": "VS4242"}
    def margins(self, seg):
        return {"net": 5000.0, "available": {"live_balance": 4200.0}}
real_kite, real_exchange, real_status = kite_auth._kite, kite_auth.exchange, kite_auth.token_status
kite_auth._kite = lambda api_key, access=None: FakeKite(api_key, access)
kite_auth.exchange = lambda key, secret, rt: (used.append(("exchange", key, secret, rt))
                                              or ("friend-access" if key == FKEY else "owner-access"))
kite_auth.token_status = lambda key, tok: (used.append(("status", key, tok)) or ("ok", "fine"))
try:
    url = uk.login_url("nonce1", FRIEND)
    check("the friend's login page carries HIS api_key", "api_key=friendkey9xyz" in url and "redirect_params=" in url, url)
    check("the owner's login page still carries the server's", "api_key=serverkey1234" in uk.login_url("nonce2", OWNER))
    ok, msg = uk.connect(FRIEND, "req-tok")
    check("the request token is exchanged with HIS key and secret", ok and ("exchange", FKEY, FSECRET, "req-tok") in used, (ok, msg))
    check("...and who it was is read under his app", ("kite", FKEY, "friend-access") in used and "VS4242" in msg, msg)
    uk.status(FRIEND, force=True)
    check("the daily token check uses his key", ("status", FKEY, "friend-access") in used)
    uk.funds(FRIEND, force=True)
    check("his funds are read under his key", used[-1] == ("kite", FKEY, "friend-access"), used[-1])
    accounts.update_user(OWNER, {"kite_token": "owner-access", "kite_connected": uk._today_ist()})
    uk.connect(OWNER, "req-owner")
    check("the owner still exchanges with the SERVER's key and secret",
          ("exchange", "serverkey1234", "serversecret0123456789abcd", "req-owner") in used)
    ok, why = uk.app_ready(FRIEND)
    check("his own app is enough to connect, whatever the server has", ok, why)
    config.KITE_API_KEY, saved_key = "", config.KITE_API_KEY
    check("...even with no server app at all", uk.app_ready(FRIEND)[0] is True and uk.app_ready(OWNER)[0] is False)
    config.KITE_API_KEY = saved_key
finally:
    kite_auth._kite, kite_auth.exchange, kite_auth.token_status = real_kite, real_exchange, real_status

print("4. THE FEED, LIVE ORDERS AND THE AI DESK ASK FOR THE ACCOUNT'S KEY")
here = os.path.dirname(os.path.abspath(__file__))
FS = open(os.path.join(here, "feeds.py")).read()
check("feeds.py: no Kite call on the server's key any more", "config.KITE_API_KEY" not in FS)
check("...every data provider and tick socket is built on the account's own key",
      FS.count("KiteDataProvider(user_kite.api_key_for(self.email), token)") == 8
      and FS.count("KiteStreamer(user_kite.api_key_for(self.email), token)") == 2)
AD = open(os.path.join(here, "ai_desk.py")).read()
check("ai_desk: the option token is looked up under the account's key",
      "KiteDataProvider(user_kite.api_key_for(self.feed.email), token).option_token(" in AD)
made = []
fake_kc = types.ModuleType("kiteconnect")
class _KC:
    def __init__(self, api_key):
        self.api_key = api_key
        made.append(api_key)
    def set_access_token(self, t):
        self.token = t
fake_kc.KiteConnect = _KC
real_kc = sys.modules.get("kiteconnect")
sys.modules["kiteconnect"] = fake_kc
try:
    import live_orders
    k = live_orders._kite_factory(FRIEND)()
    check("a live order for him is sent through HIS app (his key, his token)",
          made[-1] == FKEY and k.token == "friend-access", (made, getattr(k, "token", None)))
    k = live_orders._kite_factory(OWNER)()
    check("...and the owner's through the server's, as before", made[-1] == "serverkey1234" and k.token == "owner-access")
finally:
    if real_kc is not None:
        sys.modules["kiteconnect"] = real_kc
    else:
        sys.modules.pop("kiteconnect", None)

print("5. REMOVING IT GOES BACK TO THE SERVER'S APP AND DROPS ITS TOKEN")
uk.remove_app(FRIEND)
check("back to the server's app", uk.app_for(FRIEND)[2] is False and uk.api_key_for(FRIEND) == "serverkey1234")
check("...and the token that belonged to his app is gone", uk.token_for(FRIEND) is None)

print("6. THE CONNECT PAGE")
import nbs_site
cb = config.web_callback_url()
html = nbs_site.connect_page(FRIEND, "missing", "Not connected yet.", callback_url=cb)
check("without an app: why ('user is not enabled for the app'), the Redirect URL to set, a key + secret form",
      "user is not enabled for the app" in html and cb in html and 'name="kite_api_key"' in html
      and 'name="kite_api_secret"' in html and 'type="password"' in html and 'value="save_app"' in html)
check("...and that live orders need his OWN static IP, the server's being the owner's",
      "static IP" in html and "nobody else uses" in html and "immediate family" in html)
html2 = nbs_site.connect_page(FRIEND, "ok", "fine", own_app=True, app_key_tail="9xyz", callback_url=cb)
check("with an app: whose (last 4), the Redirect URL, a remove button - no secret field",
      "9xyz" in html2 and cb in html2 and 'value="remove_app"' in html2 and "kite_api_secret" not in html2)

import web_server
def handler(user=FRIEND, same_origin=True, live_open=False):
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._redirect = lambda where: out.update(redirect=where)
    h._current_user = lambda: user
    h._same_origin = lambda: same_origin
    h._live_indian_open = lambda u: live_open
    return h, out
woke = []
real_wake = web_server.feeds.wake
web_server.feeds.wake = lambda u: woke.append(u)
try:
    h, out = handler(same_origin=False)
    h._do_connect({"action": "save_app", "kite_api_key": FKEY, "kite_api_secret": FSECRET})
    check("a cross-site post cannot set an app", "Refused" in out["body"] and uk.app_for(FRIEND)[2] is False)
    h, out = handler(live_open=True)
    h._do_connect({"action": "save_app", "kite_api_key": FKEY, "kite_api_secret": FSECRET})
    check("not while a live Zerodha position is open (its exits need today's login)",
          "still open" in out["body"] and uk.app_for(FRIEND)[2] is False)
    h, out = handler()
    h._do_connect({"action": "save_app", "kite_api_key": FKEY, "kite_api_secret": FSECRET})
    check("saved from the page: kept, the page says so, the feed is woken, the secret not echoed",
          uk.app_for(FRIEND)[2] is True and "saved" in out["body"] and woke[-1] == FRIEND and FSECRET not in out["body"])
    h, out = handler(live_open=True)
    h._do_connect({"action": "remove_app"})
    check("removing is refused too while a live position is open", uk.app_for(FRIEND)[2] is True)
    h, out = handler()
    h._do_connect({"action": "remove_app"})
    check("removed from the page", uk.app_for(FRIEND)[2] is False and "removed" in out["body"])
    h, out = handler(user=None)
    h._do_connect({"action": "save_app"})
    check("signed out: sent to the login page", out.get("redirect") == "/login")
finally:
    web_server.feeds.wake = real_wake

class _Ex:
    def __init__(self, states):
        self.positions = {f"t{i}": {"state": st} for i, st in enumerate(states)}
real_for_user = web_server.feeds.for_user
try:
    h, _ = handler()
    del h._live_indian_open
    for states, want in ((["open"], True), (["exiting"], True), (["closed", "failed"], False), ([], False)):
        web_server.feeds.for_user = lambda u, m, start=True, st=states: types.SimpleNamespace(live=_Ex(st))
        check(f"a live position {states or 'none'} -> {'blocks' if want else 'does not block'} changing the app",
              h._live_indian_open(FRIEND) is want)
    web_server.feeds.for_user = lambda u, m, start=True: types.SimpleNamespace(live=None)
    check("no live executor at all: does not block", h._live_indian_open(FRIEND) is False)
finally:
    web_server.feeds.for_user = real_for_user

print()
print("USER KITE APP TEST PASSED" if not fails else f"USER KITE APP TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
