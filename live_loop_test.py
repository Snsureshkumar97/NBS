#!/usr/bin/env python3
"""The fast loop: one tick thread per feed however many times it is started, and
a fault in the live recompute recorded, exposed and logged once - not swallowed."""
import contextlib
import io
import os
import sys
import tempfile
import threading
import time

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()           # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import feeds
import signal_engine

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. ONE TICK LOOP")
f = feeds.Feed("t:loop", "test@example.invalid", "nse_index")
runs = []
def fake_loop():
    runs.append(threading.current_thread().name)
    time.sleep(1.2)
f._tick_loop = fake_loop
starters = [threading.Thread(target=f._ensure_ticker) for _ in range(8)]
for t in starters:
    t.start()
for t in starters:
    t.join()
time.sleep(0.1)
alive = [t for t in threading.enumerate() if t.name == f"ticks:{f.key}" and t.is_alive()]
check("eight starts at once make one loop", len(alive) == 1 and len(runs) == 1, (len(alive), len(runs)))
f._ensure_ticker(); time.sleep(0.1)
check("starting again while it runs does nothing", len(runs) == 1)
time.sleep(1.3)
f._ensure_ticker(); time.sleep(0.1)
check("a loop that has ended is started again", len(runs) == 2, len(runs))


print("2. A FAULT IN THE LIVE RECOMPUTE IS SAID, NOT SWALLOWED")
class QuietStreamer:
    connected = False
    def forming_bar(self, tok): return None
    def price(self, tok): return None
    def age_seconds(self): return None
    def book(self, tok, max_age=None): return None

g = feeds.Feed("t:live", "test@example.invalid", "nse_index")
g.streamer = QuietStreamer()
df = bt.fetch_history("NIFTY", years=3, use_cache=True).iloc[-300:]
g.base_df["NIFTY"] = df
g.base_df["SENSEX"] = df.iloc[-20:]
for n in ("NIFTY", "SENSEX"):
    g.state["indices"][n] = {"rec": {}, "public": {}, "why": None, "df": None, "notes": [], "at": "00:00:00"}

real = signal_engine.compute_technical_signal
def broken(d):
    raise ValueError("broken indicator")
signal_engine.compute_technical_signal = broken
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    g._live_analysis()
    g._live_analysis()
signal_engine.compute_technical_signal = real
log = buf.getvalue()
check("the failing index is recorded with the reason",
      "broken indicator" in (g.live_errors.get("NIFTY") or ("",))[0], g.live_errors.get("NIFTY"))
check("too few candles is recorded too",
      "only 20 candles" in (g.live_errors.get("SENSEX") or ("",))[0], g.live_errors.get("SENSEX"))
said = [ln for ln in log.splitlines() if ln.startswith("[live]") and "broken indicator" in ln]
check("written to the log once, with its traceback, not every second",
      len(said) == 1 and "Traceback" in log, log[:200])
st = g._live_status()
check("exposed as status: errors named, no successful run yet",
      "NIFTY" in st["errors"] and "SENSEX" in st["errors"] and st["age"] is None, st)
check("/api/tick's payload carries it", "live_analysis" in g.ticks())


print("3. A GOOD RECOMPUTE CLEARS ITS OWN FAULT")
with contextlib.redirect_stdout(io.StringIO()):
    g._live_analysis()
st = g._live_status()
check("NIFTY recomputed: its fault is gone and the age is fresh",
      "NIFTY" not in st["errors"] and st["age"] is not None and st["age"] < 5, st)
check("SENSEX still has too few candles, and still says so", "SENSEX" in st["errors"])
check("the state was updated", g.state["indices"]["NIFTY"]["at"] != "00:00:00")

print("4. ENOUGH CANDLES AFTER ANY HOLIDAY BREAK")
import datetime as dt
import inspect
import config
import main
days = config.INTRADAY_LOOKBACK_DAYS
need = max(60, config.EMA_SLOW + 5)
bars_per_session = 24                 # 09:30 to 15:15 once the pre-open candle is dropped
worst = None
d = dt.date(2026, 1, 1)
while d <= dt.date(2026, 12, 31):
    if d.weekday() < 5 and not main.is_nse_holiday(d):
        # The morning's first pass: today's range has barely begun, so count
        # only the complete sessions inside the window before today.
        sessions = sum(1 for k in range(1, days + 1)
                       if (d - dt.timedelta(days=k)).weekday() < 5
                       and not main.is_nse_holiday(d - dt.timedelta(days=k)))
        if worst is None or sessions < worst[0]:
            worst = (sessions, d)
    d += dt.timedelta(days=1)
check(f"{days} calendar days leave at least {need} candles on every 2026 trading morning",
      worst[0] * bars_per_session >= need, f"worst: {worst[0]} sessions before {worst[1]}")
five = min(sum(1 for k in range(1, 6) if (dd - dt.timedelta(days=k)).weekday() < 5
               and not main.is_nse_holiday(dd - dt.timedelta(days=k)))
           for dd in [dt.date(2026, 9, 15)])
check("the old five days left too few on 15 Sep 2026 - the morning this was found",
      five * bars_per_session < need, f"{five} sessions")
src = inspect.getsource(feeds.Feed._run)
check("the analysis pass asks for that many days for the Indian indices",
      "INTRADAY_LOOKBACK_DAYS" in src and 'self.market == "nse_index"' in src)

print("LIVE LOOP TEST PASSED" if not fails else f"LIVE LOOP TEST FAILED: {fails}")
