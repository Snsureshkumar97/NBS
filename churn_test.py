"""Replay a choppy session and count the tickets it produces."""
import sys, os, csv, tempfile, datetime as dt, random
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, config, trade_log

CLOCK = {"t": 0.0}
gui.time.time = lambda: CLOCK["t"]
# The reasoning panel wants a fully-populated rec; this test is about how MANY
# tickets get issued, not what they say.
gui.explain.explain = lambda rec: {"verdict": "test", "lines": [], "levels": [],
                                   "headline": "test"}

def build():
    path = os.path.join(tempfile.mkdtemp(), "trades.csv")
    with open(path, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=trade_log.FIELDS).writeheader()
    trade_log._log_path = lambda: path
    root = tk.Tk(); a = gui.SignalApp(root)
    a.popup_var.set(False); root.bell = lambda: None
    a.limits_var.set(False)          # limits OFF, as you asked for
    return a

def rec_for(side, i):
    ce = side == "CE"
    return {"index": "NIFTY", "bias": "BULLISH" if ce else "BEARISH",
            "option_type": side, "suggested_strike": 24500, "spot": 24480.0 + i,
            "index_targets": [24548., 24612., 24690.] if ce else [24420., 24380., 24340.],
            "index_stop_loss": 24402.0 if ce else 24560.0,
            "premium_targets": [214., 246., 285.], "premium_stop_loss": 152.,
            "premium_source": None, "live_ltp": None,
            "candles": None, "option_chain": None}

def run_session(label):
    """A real trading day at the live cadence: 250ms evaluations for 6h15m,
    with the bias wobbling the way a choppy market makes it wobble."""
    app = build()
    rnd = random.Random(4)
    CLOCK["t"] = 0.0
    gui.now_ist = lambda: dt.datetime(2026, 9, 1, 9, 20) + dt.timedelta(seconds=CLOCK["t"])
    side, tickets, resolve_at = "CE", 0, 0.0
    steps = int(6.25 * 3600 / 0.25)          # 09:20 -> 15:30 at 250ms
    for i in range(steps):
        CLOCK["t"] += 0.25
        # flip direction on average every ~3 minutes, as chop does
        if rnd.random() < 0.25 / 180:
            side = "PE" if side == "CE" else "CE"
        # A trade that has been issued RUNS. Closing it in the same instant
        # made brake 3 unmeasurable, because there was never an open position
        # for a flip to destroy.
        if app.active_trade is not None and CLOCK["t"] >= resolve_at:
            app.active_trade["status"] = "CLOSED — T3 hit (full target reached)"
            app._log_trade_closed(app.active_trade, 210.0)
            app.active_trade = None
        if app._consider_signal(rec_for(side, i)):
            tickets += 1
            resolve_at = CLOCK["t"] + 12 * 60      # resolves in ~12 minutes
    print(f"  {label:38s} {tickets:3d} tickets")
    return tickets

print("\nA choppy 6h15m session, direction wobbling every ~3 minutes:\n")

# --- the settings that produced 77 -------------------------------------
config.SIGNAL_CONFIRM_SECONDS = 0
config.MIN_MINUTES_BETWEEN_TICKETS = 0
config.CLOSE_ON_SIGNAL_FLIP = True
before = run_session("old behaviour (1s confirm, no gap)")

# --- each brake on its own ---------------------------------------------
config.SIGNAL_CONFIRM_SECONDS = 120
only_confirm = run_session("+ 120s confirmation")

config.SIGNAL_CONFIRM_SECONDS = 0
config.MIN_MINUTES_BETWEEN_TICKETS = 20
only_gap = run_session("+ 20min gap between tickets")

config.SIGNAL_CONFIRM_SECONDS = 0
config.MIN_MINUTES_BETWEEN_TICKETS = 0
config.CLOSE_ON_SIGNAL_FLIP = False
only_hold = run_session("+ flip no longer closes a trade")

# --- all three, the shipped defaults ------------------------------------
config.SIGNAL_CONFIRM_SECONDS = 120
config.MIN_MINUTES_BETWEEN_TICKETS = 20
config.CLOSE_ON_SIGNAL_FLIP = False
after = run_session("ALL THREE (the new defaults)")

print(f"\n  {before} -> {after} tickets  ({100*(before-after)//max(1,before)}% fewer)")
assert after < before / 4, f"not enough of a reduction: {before} -> {after}"
assert after >= 1, "the brakes stopped every trade, which is not the goal"

# --- and a genuine, sustained signal must STILL get through -------------
config.SIGNAL_CONFIRM_SECONDS = 120
app = build()
CLOCK["t"] = 0.0
gui.now_ist = lambda: dt.datetime(2026, 9, 1, 10, 0) + dt.timedelta(seconds=CLOCK["t"])
took = False
for i in range(2000):
    CLOCK["t"] += 0.25
    if app._consider_signal(rec_for("CE", i)):
        took = True
        print(f"\n  a signal held steady -> ticket issued after "
              f"{CLOCK['t']:.0f}s ({CLOCK['t']/60:.1f} min)")
        break
assert took, "a steady signal never became a ticket"
assert 110 <= CLOCK["t"] <= 135, f"confirmed at {CLOCK['t']}s, expected ~120"

print("\nCHURN TEST PASSED")
