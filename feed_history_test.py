"""Constituent history: the cache covers what was asked, serves while it
refreshes when that is safe, and warms itself. A fake Zerodha, a temp home."""
import os, sys, tempfile, threading, time
os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import feeds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

CALLS = []
class FakeKite:
    def __init__(self, key, token): pass
    def equity_tokens(self, symbols): return {"AAA": 1, "BBB": 2}
    def candles_for_token(self, tok, interval="day", days=90):
        CALLS.append((interval, days)); time.sleep(0.2)
        n = days if interval == "day" else days * 75
        return pd.DataFrame({"close": [100.0 + i for i in range(n)]})
feeds.KiteDataProvider = FakeKite
feeds.user_kite.token_for = lambda email: "tok"
SHUT = {"v": True}
feeds.is_market_open = lambda now, ref=None: not SHUT["v"]

f = feeds.Feed("t:nse", "test@example.invalid", "nse_index")
f.eq_tokens = {"AAA": 1, "BBB": 2}
t0 = time.time()

print("1. COVERING")
h = f.constituent_history("day", days=120)
check("cold read fetches", len(CALLS) == 2 and len(h["AAA"]) == 120, CALLS)
f.constituent_history("day", days=90)
check("a shorter request is answered from the cache", len(CALLS) == 2)
h = f.constituent_history("day", days=400)
check("a LONGER request is not handed the 120-day set", len(CALLS) == 4 and len(h["AAA"]) == 400, (len(CALLS), len(h["AAA"])))
f.constituent_history("day", days=120)
check("and the 400-day set then answers 120", len(CALLS) == 4)

print("2. SERVE WHILE REFRESHING - MARKET SHUT")
ts, data, days = f._hist_cache["day"]
f._hist_cache["day"] = (ts - 3600, data, days)                  # an hour old: past the 15-minute refresh
s = time.time(); h = f.constituent_history("day", days=400); took = time.time() - s
check("returns at once, from the cache", took < 0.05 and len(h["AAA"]) == 400, f"{took*1000:.0f} ms")
time.sleep(0.8)
check("and one refresh ran in the background", len(CALLS) == 6, len(CALLS))
check("the cache is fresh again", time.time() - f._hist_cache["day"][0] < 5)
ts, data, days = f._hist_cache["day"]
f._hist_cache["day"] = (ts - 7 * 3600, data, days)              # seven hours: too old even when shut
s = time.time(); f.constituent_history("day", days=400); took = time.time() - s
check("past six hours it waits for a fresh read", took >= 0.35 and len(CALLS) == 8, f"{took:.2f}s")

print("3. WHILE THE MARKET TRADES")
SHUT["v"] = False
ts, data, days = f._hist_cache["day"]
f._hist_cache["day"] = (ts - 1200, data, days)                   # 20 min: under 2x the refresh time
s = time.time(); f.constituent_history("day", days=400); took = time.time() - s
check("20 minutes old: served at once, refreshed behind", took < 0.05, f"{took*1000:.0f} ms")
time.sleep(0.8)
ts, data, days = f._hist_cache["day"]
f._hist_cache["day"] = (ts - 3600, data, days)                   # an hour: too old during a session
before = len(CALLS); s = time.time(); f.constituent_history("day", days=400); took = time.time() - s
check("an hour old during a session: waits for fresh figures", took >= 0.35 and len(CALLS) == before + 2, f"{took:.2f}s")

print("4. ONE REFRESH AT A TIME")
SHUT["v"] = True
ts, data, days = f._hist_cache["5minute"] if "5minute" in f._hist_cache else (None, None, None)
f.constituent_history("5minute", days=5)
ts, data, days = f._hist_cache["5minute"]
f._hist_cache["5minute"] = (ts - 600, data, days)
before = len(CALLS)
for _ in range(5):
    f.constituent_history("5minute", days=5)
time.sleep(0.8)
check("five stale reads start one refresh, not five", len(CALLS) == before + 2, len(CALLS) - before)

print("5. WARM-UP")
g = feeds.Feed("t2:nse", "test2@example.invalid", "nse_index")
g.eq_tokens = {"AAA": 1, "BBB": 2}
feeds.time_sleep_real = time.sleep
orig_sleep = feeds.time.sleep
before = len(CALLS)
feeds.time.sleep = lambda s: orig_sleep(min(s, 0.01))            # skip the 15 s head start
try:
    g.warm_history(); g.warm_history()
    for _ in range(100):
        if "5minute" in g._hist_cache and "day" in g._hist_cache:
            break
        orig_sleep(0.05)
finally:
    feeds.time.sleep = orig_sleep
check("warms 400 days of daily and 5 days of 5-minute candles, once", g._hist_cache.get("day", (0, 0, 0))[2] == 400 and g._hist_cache.get("5minute", (0, 0, 0))[2] == 5 and len(CALLS) - before == 4, len(CALLS) - before)
c = feeds.Feed("t3:crypto", "test3@example.invalid", "crypto")
c.warm_history()
check("a crypto feed does not warm Indian constituents", c._hist_warmed is False)

print()
print("FEED HISTORY TEST PASSED" if not fails else f"FEED HISTORY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
