#!/usr/bin/env python3
"""The Signal page's option-chain drawing must not blink away (the user, 25 Oct 2026: "when i using it in my phone the
signal tab keeps loading because of the option chain thing and i could not scroll").

Sellers above / support below, who moved, and the CPR are worked out only by the per-second recompute
(Feed._live_analysis). The 30-second analysis pass (Feed._run) publishes a reading of its own without them - so about
every 30 seconds the page got a reading with no sellers, emptied the whole drawing (about 1,100px on a phone), and drew
it again a second later: the page shrank under the reader's finger and threw them up the page. The analysis pass now
keeps the per-second recompute's last ones while that recompute is running, and drops them when it has stopped."""
import contextlib
import copy
import io
import os
import sys
import tempfile
import time

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

import backtest_intraday as bt
import feeds
import signal_engine

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra if not cond else ''}")
    if not cond:
        fails.append(name)


class Streamer:
    connected = True
    def __init__(self, bar):
        self.bar = bar
    def forming_bar(self, tok): return self.bar
    def price(self, tok): return self.bar["c"]
    def age_seconds(self): return 0.2
    def book(self, tok, max_age=None): return None


base = bt.fetch_history("NIFTY", years=3, use_cache=True).iloc[-300:]
last = float(base["Close"].iloc[-1])
atm = round(last / 50) * 50


def chain(oi_add=0.0, px_add=0.0):
    strikes = [{"strike": float(k), "call_oi": 2e6 + (k - atm + 600) * 4e3 + oi_add, "put_oi": 2e6 + (atm + 600 - k) * 4e3 + oi_add,
                "call_ltp": max(5.0, atm - k + 90) + px_add, "put_ltp": max(5.0, k - atm + 90) + px_add}
               for k in range(atm - 600, atm + 650, 50)]
    return signal_engine.compute_option_chain_signal({"strikes": strikes, "spot": last, "expiry": "2026-10-28", "pcr": 1.0,
                                                      "max_pain": atm, "top_call_oi_strike": atm + 600,
                                                      "top_put_oi_strike": atm - 600})


f = feeds.Feed("t:blink", "test@example.invalid", "nse_index")
start = int((base.index[-1] + pd.Timedelta(minutes=15)).timestamp())
f.streamer = Streamer({"start": start, "o": last, "h": last + 5, "l": last - 5, "c": last})
f.base_df["NIFTY"] = base
f.base_oi["NIFTY"] = chain(oi_add=1.5e5, px_add=8.0)
f.tokens["NIFTY"] = 1
f.instruments = lambda: ["NIFTY"]
f.state["indices"]["NIFTY"] = {"rec": {}, "public": {}, "why": None, "df": None, "notes": [], "at": "00:00:00"}
# fifteen minutes of open-interest samples already kept, so who-moved has something to compare with
then = {float(s["strike"]): (s["call_oi"], s["put_oi"], s["call_ltp"], s["put_ltp"]) for s in chain()["strikes"]}
f.oi_hist["NIFTY"] = [(time.time() - feeds.SELLERS_LOOKBACK_S, then)]

print("1. THE PER-SECOND RECOMPUTE WORKS THEM OUT")
with contextlib.redirect_stdout(io.StringIO()):
    f._live_analysis()
live = f.state["indices"]["NIFTY"]["public"]
check("sellers, who moved and the CPR are on the reading", live.get("sellers") and live.get("buildup") and live.get("cpr"),
      {k: bool(live.get(k)) for k in ("sellers", "buildup", "cpr")})
live_rec = f.state["indices"]["NIFTY"]["rec"]


def one_pass():
    """One trip round Feed._run with the analysis pass's own reading - main.fetch_recommendation's, which has none of
    the three - and back out."""
    rest = {k: v for k, v in copy.copy(live_rec).items() if k not in ("sellers", "buildup", "cpr")}
    passes = []
    feeds.fetch_recommendation = lambda *a, **k: (dict(rest), [])
    f._start_stream = lambda: None
    f._provider = lambda: (object(), "ok", "")
    f._provider_for = lambda name, p: p
    f._sleep = lambda s: passes.append(s)
    f._idle = lambda: bool(passes)
    real_sleep, feeds.time.sleep = feeds.time.sleep, lambda s: None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            f._run()
    finally:
        feeds.time.sleep = real_sleep
    return f.state["indices"]["NIFTY"]


print("2. THE 30-SECOND ANALYSIS PASS KEEPS THEM")
seq0 = f.state["indices"]["NIFTY"].get("seq", 0)
e = one_pass()
p = e["public"]
check("the pass did publish its own reading", e.get("seq", 0) > seq0 and e["rec"] is not live_rec)
check("its reading still has the sellers - the drawing is not emptied", p.get("sellers") == live["sellers"], p.get("sellers"))
check("and who moved, and the CPR", p.get("buildup") == live["buildup"] and p.get("cpr") == live["cpr"])
r = f.reading("NIFTY")
check("the page's fast poll gets them on that reading too", r and (r["public"] or {}).get("sellers") == live["sellers"])

print("3. NOT ONCE THE PER-SECOND RECOMPUTE HAS STOPPED")
f.live_at = time.time() - 120
p = one_pass()["public"]
check("two minutes without a recompute: the pass shows none rather than old ones",
      p.get("sellers") is None and p.get("buildup") is None and p.get("cpr") is None,
      {k: bool(p.get(k)) for k in ("sellers", "buildup", "cpr")})

print()
print("CHAIN BLINK TEST PASSED" if not fails else f"CHAIN BLINK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
