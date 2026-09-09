"""The seven bugs the full audit turned up. Each one, pinned."""
import sys, os, csv, tempfile, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, config, trade_log

CLOCK = {"t": 0.0}
gui.time.time = lambda: CLOCK["t"]
gui.explain.explain = lambda rec: {"verdict":"x","lines":[],"levels":[],"headline":"x"}

def fresh(hh=10, mm=0):
    path = os.path.join(tempfile.mkdtemp(), "trades.csv")
    with open(path, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=trade_log.FIELDS).writeheader()
    trade_log._log_path = lambda: path
    CLOCK["t"] = 0.0
    gui.now_ist = lambda: dt.datetime(2026,9,1,hh,mm) + dt.timedelta(seconds=CLOCK["t"])
    root = tk.Tk(); a = gui.SignalApp(root)
    a.popup_var.set(False); root.bell = lambda: None
    return a, path

REC = {"index":"NIFTY","bias":"BULLISH","option_type":"CE","suggested_strike":24500,
       "spot":24480.0,"index_targets":[24548.,24612.,24690.],"index_stop_loss":24402.,
       "premium_targets":[214.,246.,285.],"premium_stop_loss":152.,
       "premium_source":"live","live_ltp":168.2,"candles":None,"option_chain":None}
STOPPED = dict(REC, option_chain={"available":True,"strikes":[
    {"strike":24500,"call_ltp":150.0,"put_ltp":5.0,"call_oi":1,"put_oi":1}]})

def a_trade(index="NIFTY"):
    """A trade dict shaped the way _lock_new_trade builds them."""
    return {"index": index, "option_type": "CE", "strike": 24500,
            "entry_time": "10:00:00", "entry_spot": 24480.0, "entry_ltp": 168.2,
            "use_premium": True, "lot_size": 75, "lots": 1,
            "index_targets": [24548., 24612., 24690.], "index_sl": 24402.0,
            "premium_targets": [214., 246., 285.], "premium_sl": 152.0,
            "hit": {"T1": False, "T2": False, "T3": False},
            "hit_time": {"T1": None, "T2": None, "T3": None},
            "sl_hit": False, "sl_hit_time": None, "status": "OPEN",
            "trade_id": f"{index}-x"}

def opens(): return sum(1 for r in trade_log._read_rows() if r.get("event")=="OPEN")
def closes(): return [r for r in trade_log._read_rows() if r.get("event")=="CLOSE"]

# --- 1. auto re-arm must obey the daily cap and the gap ------------------
app,_ = fresh(); app.limits_var.set(True)
config.MAX_TRADES_PER_DAY = 4
app._lock_new_trade(dict(REC))
for _ in range(12):
    CLOCK["t"] += 1
    app._track_open_trade(STOPPED)
assert opens() <= config.MAX_TRADES_PER_DAY, f"re-arm bypassed the cap: {opens()} tickets"
print(f"1. auto re-arm obeys the cap        {opens()} ticket(s), was 13")

# --- 1b. and it must not machine-gun with the gap and limits BOTH off ---
# The dangerous combination: MIN_MINUTES_BETWEEN_TICKETS at 0 (removed by
# request) and the daily limits switch off. Re-arm skips the confirmation
# window, so without its own floor nothing at all separates a close from the
# next open — which is the thirteen-tickets-in-twelve-seconds bug returning by
# the back door rather than by a code change.
_gap_was = config.MIN_MINUTES_BETWEEN_TICKETS
try:
    config.MIN_MINUTES_BETWEEN_TICKETS = 0
    app, _ = fresh(); app.limits_var.set(False)
    app._lock_new_trade(dict(REC))
    before = opens()
    for _ in range(40):            # 40 evaluations across 10 simulated seconds
        CLOCK["t"] += 0.25
        app._track_open_trade(STOPPED)
    fired = opens() - before
    assert fired == 0, f"re-arm machine-gunned {fired} tickets in 10s"
    print(f"1b. no gap, no limits, still calm  {fired} extra ticket(s) in 10s")

    # ...and a genuine re-entry a minute later is still taken, so the floor is
    # a floor and not a second twenty-minute hold. A fresh OPEN ticket is put
    # back on the books first: once re-arm is refused, the index is left with
    # no open trade, so _track_open_trade returns early and never asks again —
    # which is exactly why the floor cannot lock the day out.
    CLOCK["t"] += 61
    app.active_trade = a_trade()
    app._track_open_trade(STOPPED)
    # +1, not +2: putting the ticket back on the books by hand does not log an
    # OPEN, so the only new row is the one re-arm just wrote.
    assert opens() - before == 1, \
        f"the floor blocked a real re-entry after 61s ({opens() - before})"
    print("    a real re-entry 61s later is still taken")
finally:
    config.MIN_MINUTES_BETWEEN_TICKETS = _gap_was

# --- 2. the opening window must not lock out the day --------------------
app,_ = fresh(9, 15); app.limits_var.set(False)
got = []
for _ in range(3600):
    CLOCK["t"] += 1
    if app._consider_signal(dict(REC)):
        got.append(gui.now_ist().strftime("%H:%M")); app.active_trade = None
assert got, "a 09:15 signal still locks out the whole day"
assert got[0] >= "09:20", f"issued during the opening window at {got[0]}"
print(f"2. 09:15 signal is taken at         {got[0]}, was never")

# --- 3. the queue pump survives an exception ----------------------------
app,_ = fresh()
class Boom:
    def __getitem__(self, i): raise RuntimeError("bang")
    def __len__(self): return 2
app.msg_queue.put(Boom())
calls = {"n": 0}
class R:
    # count ONLY the pump's own 150ms reschedule; the bridge also schedules
    # a 70ms repaint when the status label is written
    def after(self, ms, fn=None, *a):
        if ms == 150: calls["n"] += 1
app.root = R()
gui.SignalApp._poll_queue(app)          # must not raise
assert calls["n"] == 1, f"pump rescheduled {calls['n']} times after an error"
print(f"3. pump survives an error          rescheduled, was dead for good")

# --- 4. a nested pump must not duplicate the timer chain ----------------
app,_ = fresh()
seen = {"n": 0}
class R2:
    def after(self, ms, fn=None, *a):
        if ms == 150: seen["n"] += 1
app.root = R2()
app._in_poll = True                      # pretend a modal is up
gui.SignalApp._poll_queue(app)
assert seen["n"] == 0, "a nested pump rescheduled, doubling the tick rate"
app._in_poll = False
gui.SignalApp._poll_queue(app)
assert seen["n"] == 1
print(f"4. nested pump does not duplicate   1 chain, was 2 per dialog")

# --- 5. restart clears EVERY index, not just the visible one ------------
app,_ = fresh()
for key in app.indices:
    app._focus = key
    app.active_trade = a_trade(key)
    app.last_bias_signature = ("BULLISH","CE")
app._focus = app.current
app.worker_thread = None
err = []
try:
    app._start()
except Exception as e:
    err.append(repr(e))
left = []
for key in app.indices:
    app._focus = key
    if app.active_trade is not None:
        left.append(key)
app._focus = app.current
assert not left, (f"these indices kept a stale ticket across a restart: {left}"
                  f"  (_start raised: {err})")
print(f"5. restart clears all three         0 stale tickets, was 2")

# --- 6. an open position is squared up at the bell ----------------------
app,_ = fresh(15, 30)
app._focus = app.current
app.active_trade = a_trade()
app.last_rec = STOPPED
n = app._close_open_at_bell()
assert n == 1 and app.active_trade["status"].startswith("CLOSED"), "not squared up"
assert any("market closed" in (r.get("status") or "").lower() for r in closes()), \
    "the bell close was not written to the log"
print(f"6. open ticket squared at the bell  logged, was left OPEN forever")

# --- 7. a second Start must not spawn a second worker -------------------
app,_ = fresh()
class LiveThread:
    def is_alive(self): return True
app.worker_thread = LiveThread()
before = app.current_stop_event
app._start()
assert app.current_stop_event is before, "a second Start orphaned the first stop event"
assert "Already running" in (app.status_label.text or "")
print(f"7. second Start refused             stop event intact, was orphaned")

print("\nAUDIT TEST PASSED — all eight pinned")
