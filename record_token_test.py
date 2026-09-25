#!/usr/bin/env python3
"""record_options picks a Zerodha token from the first account that HAS one.

Running all session is automatic for every live account (25 Sep 2026), so accounts.always_on_users() is every live
account, not only the ones that had switched it on. The recorder used to take the first of them; with two accounts
and only the second connected today, that would have been "no token" - it now takes the first that has one.
No network: kiteconnect is replaced by a stand-in.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

seen = {}
class FakeKite:
    def __init__(self, api_key=None): seen["key"] = api_key
    def set_access_token(self, t): seen["token"] = t
fake = types.ModuleType("kiteconnect"); fake.KiteConnect = FakeKite
sys.modules["kiteconnect"] = fake

import accounts
import record_options
import user_kite

tokens = {"a@example.com": None, "b@example.com": "TOKEN-B", "c@example.com": "TOKEN-C"}
accounts.always_on_users = lambda: ["a@example.com", "b@example.com", "c@example.com"]
user_kite.token_for = lambda email: tokens.get(email)
record_options._kite()
check("the first account has no token today, so the second one's is used", seen.get("token") == "TOKEN-B", seen.get("token"))
tokens["a@example.com"] = "TOKEN-A"; seen.clear()
record_options._kite()
check("when the first has one, it is the first that is used", seen.get("token") == "TOKEN-A", seen.get("token"))
tokens.update({"a@example.com": None, "b@example.com": None, "c@example.com": None})
try:
    record_options._kite(); ok = False
except SystemExit as e:
    ok = "No Zerodha token today" in str(e)
check("nobody has a token: it stops with the instruction to connect Zerodha", ok)
accounts.always_on_users = lambda: []
try:
    record_options._kite(); ok = False
except SystemExit as e:
    ok = "No Zerodha token today" in str(e)
check("no accounts at all: the same", ok)
print("RECORD TOKEN TEST PASSED" if not fails else f"RECORD TOKEN TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
