"""Market-closed behaviour: the screen must keep showing the last session."""
import sys, types, datetime as dt, queue
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import pandas as pd, numpy as np

import gui

# --- a stand-in app that only has the bits _worker/_poll_queue touch -----
class Fake:
    pass

def make_df(n=200, end="2026-08-18 15:30"):
    idx = pd.date_range(end=end, periods=n, freq="15min")
    base = 24000 + np.cumsum(np.random.RandomState(1).randn(n) * 8)
    return pd.DataFrame({"Open": base, "High": base + 12, "Low": base - 12,
                         "Close": base, "Volume": 1000}, index=idx)

DF = make_df()

calls = {"fetch": 0}
def fake_fetch(provider, index_key, interval, lookback, quiet=True, expiry=None):
    calls["fetch"] += 1
    return {"index": index_key, "candles": DF, "spot": float(DF["Close"].iloc[-1]),
            "action": "NO TRADE", "trend": {"label": "SIDEWAYS"}}, []

gui.fetch_recommendation = fake_fetch
gui.format_report = lambda rec: f"REPORT for {rec['index']}"

# --- market closed ------------------------------------------------------
CLOSED = dt.datetime(2026, 8, 18, 19, 0)   # 19:00 IST, well after the bell
OPEN   = dt.datetime(2026, 8, 18, 11, 0)
clock = {"now": CLOSED}
gui.now_ist = lambda: clock["now"]
gui.is_market_open = lambda now: dt.time(9, 15) <= now.time() <= dt.time(15, 30)
gui.format_market_closed_banner = lambda now: "Market is closed.\nOpens 09:15 IST."

app = Fake()
app.msg_queue = queue.Queue()
app.was_open_last_check = None
app._closed_snapshot_done = False
app.cycle_count = 0
app.provider = object()
app._sleep_interruptible = lambda s, e: None

class Stop:
    def __init__(self): self.n = 0
    def is_set(self):
        self.n += 1
        return self.n > 400        # let a couple of loops run, then stop
stop = Stop()

gui.SignalApp._worker(app, ["NIFTY", "BANKNIFTY"], 60, stop, None)

msgs = []
while not app.msg_queue.empty():
    msgs.append(app.msg_queue.get())
kinds = [m[0] for m in msgs]
reports = [m for m in msgs if m[0] == "report"]

print("messages:", kinds[:8], "...")
assert "closed" in kinds, "no closed banner"
assert len(reports) == 2, f"expected one snapshot per index, got {len(reports)}"
for r in reports:
    assert len(r) == 6 and r[5] is True, "snapshot must be flagged stale"
print(f"closed-market snapshot fetched for {len(reports)} indices, flagged stale")

# --- it must fetch ONCE, not every cycle --------------------------------
assert calls["fetch"] == 2, f"refetched while closed: {calls['fetch']} calls"
print("fetched once per closed period, not every cycle:", calls["fetch"], "calls")

# --- reopening re-arms the snapshot -------------------------------------
clock["now"] = OPEN
app2 = Fake(); app2.msg_queue = queue.Queue()
app2.was_open_last_check = False; app2._closed_snapshot_done = True
app2.cycle_count = 0; app2.provider = object()
app2._sleep_interruptible = lambda s, e: None
s2 = Stop(); s2.n = 380
gui.SignalApp._worker(app2, ["NIFTY"], 60, s2, None)
assert app2._closed_snapshot_done is False, "reopening did not re-arm the snapshot"
live = [m for m in [app2.msg_queue.get() for _ in range(app2.msg_queue.qsize())]
        if m[0] == "report"]
assert live and len(live[0]) == 5, "live reports must NOT be flagged stale"
print("market reopened: reports are live again, snapshot re-armed")

# --- a failing fetch must retry, not give up ----------------------------
def boom(*a, **k):
    raise RuntimeError("network down")
gui.fetch_recommendation = boom
clock["now"] = CLOSED
app3 = Fake(); app3.msg_queue = queue.Queue()
app3.was_open_last_check = None; app3._closed_snapshot_done = False
app3.cycle_count = 0; app3.provider = object()
app3._sleep_interruptible = lambda s, e: None
s3 = Stop(); s3.n = 390
gui.SignalApp._worker(app3, ["NIFTY"], 60, s3, None)
assert app3._closed_snapshot_done is False, "gave up after one failed fetch"
print("failed fetch leaves the snapshot armed for another try")

print("\nMARKET-CLOSED WORKER TEST PASSED")
