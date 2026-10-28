#!/usr/bin/env python3
"""The option recorder's strike window (the user, 28 Oct 2026: "widen the recorder and start collecting"): every strike
within 6% of the day's range is kept, so option selling (condors two expected moves out, 4-5% away) can be tested on
real prices - and nothing the narrow window kept is lost. No network."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

import record_options as ro

print("1. THE WINDOW")
for key, low, high, step in (("NIFTY", 22430.0, 22830.0, 50), ("BANKNIFTY", 53500.0, 54900.0, 100),
                             ("SENSEX", 72700.0, 74100.0, 100), ("MIDCPNIFTY", 13050.0, 13400.0, 25)):
    lo, hi = ro.strike_window(low, high, step)
    old_lo, old_hi = (low // step - ro.MARGIN_STEPS) * step, (high // step + 1 + ro.MARGIN_STEPS) * step
    check(f"{key}: reaches 6% beyond the day's range on both sides", lo <= low * (1 - ro.WIDE_PCT) and hi >= high * (1 + ro.WIDE_PCT),
          (lo, hi))
    check(f"{key}: keeps everything the narrow window kept", lo <= old_lo and hi >= old_hi)
    check(f"{key}: on the strike grid, and no more than one strike past 6%",
          lo % step == 0 and hi % step == 0 and lo > low * (1 - ro.WIDE_PCT) - step and hi < high * (1 + ro.WIDE_PCT) + step)
check("WIDE_PCT is 6%", ro.WIDE_PCT == 0.06)

print("2. THE RECORDER USES IT")
src = open(os.path.join(HERE, "record_options.py")).read()
check("record() takes its strikes from strike_window, on the day's own low and high",
      'lo, hi = strike_window(spot["low"].min(), spot["high"].max(), meta["strike_step"])' in src
      and 'lo <= i["strike"] <= hi' in src)
check("still read-only: it asks Zerodha for prices and places nothing",
      "place_order" not in src and "historical_data" in src)

print("3. A PASSING KITE FAILURE IS ASKED AGAIN (the user, 28 Oct 2026: \"yes add the retry\")")
import datetime as dt
waits = []
ro.time.sleep = waits.append                                  # no real waiting in a test
class FakeKite:
    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0
    def historical_data(self, token, frm, to, interval, oi=True):
        self.calls += 1
        if self.errors:
            raise Exception(self.errors.pop(0))
        return [{"date": frm, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0, "oi": 0}]
bad_gateway = "Unknown Content-Type (text/html) with response: (b'<html><title>502 Bad Gateway</title></html>')"
k = FakeKite([bad_gateway])
df = ro._hist(k, 123, dt.date(2026, 10, 28))
check("a 502 once: asked again, and the candles are kept", len(df) == 1 and k.calls == 2, (len(df), k.calls))
check("...after a 2-second wait", ro.RETRY_WAIT[0] in waits, waits)
k = FakeKite([bad_gateway, "Gateway Timeout 504"])
check("two passing failures: the third try keeps it", len(ro._hist(k, 123, dt.date(2026, 10, 28))) == 1 and k.calls == 3)
k = FakeKite([bad_gateway] * 3)
check("three in a row: given up, an empty frame, no fourth call", ro._hist(k, 123, dt.date(2026, 10, 28)).empty
      and k.calls == 3, k.calls)
k = FakeKite(["Invalid token"])
check("a real error (not a passing one) is not asked again", ro._hist(k, 123, dt.date(2026, 10, 28)).empty
      and k.calls == 1, k.calls)
k = FakeKite(["Too many requests"])
check("the rate limit is still asked again", len(ro._hist(k, 123, dt.date(2026, 10, 28))) == 1 and k.calls == 2)

print("4. EVERY EXPIRY WITHIN 35 DAYS (the user, 28 Oct 2026: \"do the recorder change\")")
d0 = dt.date(2026, 10, 28)
weekly = [dt.date(2026, 11, 3) + dt.timedelta(weeks=n) for n in range(10)]            # Nifty's Tuesdays
monthly = [dt.date(2026, 11, 24), dt.date(2026, 12, 29), dt.date(2027, 1, 26)]        # Bank Nifty's last Tuesdays
got = ro.pick_expiries(weekly + weekly[:3], d0)                                         # (one per contract: repeats)
check("a weekly index: every expiry to 35 days out - five, the furthest 34 days", got == weekly[:5]
      and (got[-1] - d0).days == 34, got)
check("...so options 14+ days out are kept (the nearest two reach only 13)", any((e - d0).days >= 14 for e in got)
      and all((e - d0).days < 14 for e in weekly[:2]))
check("a monthly index: never fewer than the nearest two", ro.pick_expiries(monthly, d0) == monthly[:2])
check("record() keeps them", "expiries = pick_expiries([i[\"expiry\"] for i in opts], day)" in src)

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
