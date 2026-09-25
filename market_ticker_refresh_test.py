#!/usr/bin/env python3
"""The markets strip's refresh: one at a time, one connection, and a page never waits on Yahoo.

25 Sep 2026, "the tool is getting slow": a profile of the running server (py-spy, 12 s, no restart) put ~55% of all
CPU in market_ticker.rows() called FROM REQUEST THREADS - each request that found the strip a few seconds past its 90 s
TTL fetched every market itself, on top of the background thread's own refresh and every other page's - and 44% of all
samples in the TLS setup of requests.get(), which builds a new session (and a new TLS context that parses the CA bundle
while holding the GIL) for every one of ~35 calls. The rules below are what removes that:

  - one requests.Session for every call;
  - a request gets whatever strip there is, however old, and never fetches while there is one (only the background
    thread refreshes it);
  - only when there is nothing at all, or the strip is over ten minutes old (the background thread is dead), does a
    request refresh - and then ONE refresh serves every request that arrived meanwhile;
  - a refresh that got nothing is not retried by every request that comes along.

Yahoo is replaced by a counter; no network.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_ticker as mt

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

mt.MARKETS = [("^A", "A", 2, "india"), ("^B", "B", 2, "world"), ("^C", "C", 2, "crypto")]
calls = {"n": 0}
mode = {"fail": False, "slow": 0.0}
def fake_one(ticker, label, dp):
    calls["n"] += 1
    if mode["slow"]:
        time.sleep(mode["slow"])
    if mode["fail"]:
        raise RuntimeError("yahoo is down")
    return {"label": label, "price": 1.0, "change": 0.0, "pct": 0.0, "dp": dp}
mt._one = fake_one
def reset(age=None, rows=True):
    calls["n"] = 0
    mt._cache["rows"] = [{"label": "old", "price": 9.0, "change": 0, "pct": 0, "dp": 2, "group": "world"}] if rows else []
    mt._cache["at"] = (time.time() - age) if (age is not None and rows) else 0.0
    mt._cache["failed_at"] = 0.0
    mode["fail"] = False; mode["slow"] = 0.0

print("1. ONE SESSION")
import requests
check("the module holds one Session and _one goes through it", isinstance(mt._session, requests.Session))
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_ticker.py")).read()
import re
check("nothing in the module calls requests.get() (a new session, and a new TLS context, per call)",
      not re.search(r"^[^#\n]*\brequests\.get\(", src, re.M))

print("2. A REQUEST NEVER FETCHES WHILE THERE IS A STRIP")
reset(age=5)
r = mt.rows()
check("a fresh strip is returned as it is", calls["n"] == 0 and r[0]["label"] == "old")
reset(age=200)                       # past the 90 s TTL - this is what used to trigger a fetch in the request
r = mt.rows()
check("past the TTL it is STILL just returned: the background thread's job to refresh it", calls["n"] == 0 and r[0]["label"] == "old", calls["n"])
reset(age=550)
mt.rows()
check("even nine minutes old", calls["n"] == 0)

print("3. NOTHING AT ALL, OR THE BACKGROUND THREAD IS DEAD: ONE REFRESH SERVES EVERYONE")
reset(rows=False); mode["slow"] = 0.05
out = []
ts = [threading.Thread(target=lambda: out.append(mt.rows())) for _ in range(30)]
[t.start() for t in ts]; [t.join() for t in ts]
check("30 requests at once on an empty strip: ONE fetch of each market, not 30", calls["n"] == len(mt.MARKETS), calls["n"])
check("...and every one of them got the rows", len(out) == 30 and all(len(o) == 3 for o in out), [len(o) for o in out][:5])
reset(age=700); mode["slow"] = 0.05
out = []
ts = [threading.Thread(target=lambda: out.append(mt.rows())) for _ in range(20)]
[t.start() for t in ts]; [t.join() for t in ts]
check("a strip over ten minutes old (background thread dead): one request refreshes it, the rest are handed what there is or the result",
      calls["n"] == len(mt.MARKETS) and len(out) == 20, calls["n"])

print("4. THE BACKGROUND REFRESH")
reset(age=200); mode["slow"] = 0.05
res = []
t = threading.Thread(target=lambda: res.append(mt.rows(force=True))); t.start(); time.sleep(0.02)
r2 = mt.rows(force=True)            # a second refresh while the first is running
t.join()
check("forced while another refresh runs: does not fetch a second time", calls["n"] == len(mt.MARKETS), calls["n"])
check("...and the first one replaced the strip", res and {r["label"] for r in res[0]} == {"A", "B", "C"} and mt.rows()[0]["label"] in "ABC")
check("the strip's groups are kept on each row", {r["group"] for r in mt.rows()} == {"india", "world", "crypto"})

print("5. A REFRESH THAT GOT NOTHING")
reset(age=700); mode["fail"] = True
mt.rows()
n1 = calls["n"]
mt.rows(); mt.rows(); mt.rows()
check("Yahoo down: the first request tries, the ones after it do not (no storm of retries)", n1 == len(mt.MARKETS) and calls["n"] == n1, (n1, calls["n"]))
check("...and the old strip is still what they get", mt.rows()[0]["label"] == "old")
mt._cache["failed_at"] = time.time() - 700
mt.rows()
check("after ten more minutes one request tries again", calls["n"] == 2 * n1, calls["n"])

print("6. THE BACKGROUND LOOP KEEPS A STEADY CYCLE AND STOPS WHEN ASKED")
reset(rows=False)
stop = threading.Event()
th = mt.start_background(stop)
time.sleep(0.6)
check("it refreshed at once on start", calls["n"] == len(mt.MARKETS) and len(mt.rows()) == 3, calls["n"])
stop.set(); th.join(timeout=3)
check("and stops when asked", not th.is_alive())
check("its wait counts the refresh's own time (a steady cycle, not refresh + TTL)", "while time.time() - began < TTL:" in src)

print("MARKET TICKER REFRESH TEST PASSED" if not fails else f"MARKET TICKER REFRESH TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
