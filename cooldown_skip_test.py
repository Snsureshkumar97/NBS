"""Skip cooldown: lifts only the clock, once, and every ticket it lets through
is marked. Runs the real TicketBook against a pinned clock and a temp log."""
import csv, datetime as dt, os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, tickets, trade_log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = dt.datetime(2026, 9, 8, 11, 0, 0, tzinfo=IST)          # a Tuesday, mid-session
CLOCK = {"s": 0.0}
tickets.now_ist = lambda: BASE + dt.timedelta(seconds=CLOCK["s"])
tickets.time = types.SimpleNamespace(time=lambda: 1_780_000_000 + CLOCK["s"])
tickets.is_market_open = lambda *a, **k: True
tickets._safe_explain = lambda rec: {"verdict": "test"}
LOG = os.path.join(tempfile.mkdtemp(), "trades.csv")
trade_log._log_path = lambda: LOG

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

def rec(side="CE", spot=None, rr=2.67, spread=None):
    ce = side == "CE"
    spot = spot if spot is not None else (24500.0 if ce else 24200.0)
    return {"index": "NIFTY", "bias": "BULLISH" if ce else "BEARISH", "option_type": side,
            "suggested_strike": 24500 if ce else 24200, "spot": spot,
            "index_targets": [24560., 24610., 24660.] if ce else [24140., 24090., 24040.],
            "index_stop_loss": 24440. if ce else 24260.,
            "premium_targets": [130., 150., 170.], "premium_stop_loss": 80.,
            "premium_source": None, "live_ltp": None, "option_chain": None,
            "risk_points": 60.0, "reach_points": 160.0, "reach_to_risk": rr,
            "opening_range": {"ready": True, "high": 24400.0, "low": 24300.0},
            "spread": spread, "technical": {"adx": 30}}

book = tickets.TicketBook(owner=None, market="nse_index")
book.auto_rearm = True
def feed(r, seconds=1, n=1):
    opened = []
    for _ in range(n):
        CLOCK["s"] += seconds
        opened += [e for e in book.update("NIFTY", r) if e.get("kind") == "opened"]
    return opened
wait = lambda: (book.books["NIFTY"].wait_reason or (None,))[0]
trade = lambda: book.books["NIFTY"].trade
def stop_out():
    feed(rec(spot=24430.0))                   # through the 24440 stop
    return trade() is None or trade()["status"] != "OPEN"

print("1. NOTHING TO SKIP")
ok, msg = book.skip_cooldown("NIFTY"); check("refused before any cooldown", not ok, msg)
ok, msg = book.skip_cooldown("NOPE"); check("refused for an unknown index", not ok, msg)

print("2. FIRST TICKET, THEN A STOP-OUT STARTS THE 20 MINUTES")
check("first ticket opens after the 120s confirmation", len(feed(rec(), n=125)) == 1, str(wait()))
check("first ticket is not marked skipped", trade()["cooldown_skipped"] is False)
ok, msg = book.skip_cooldown("NIFTY"); check("refused while a ticket is open", not ok, msg)
check("stopped out", stop_out())
check("same way again is held by the cooldown", not feed(rec(), n=30) and wait() == "reentry_cooldown", str(wait()))
check("the page is told it is a cooldown", (book.public("NIFTY")["wait"] or {}).get("code") == "reentry_cooldown")

print("3. SKIP IT")
ok, msg = book.skip_cooldown("NIFTY"); check("skip accepted", ok, msg)
got = feed(rec())
check("the ticket comes on the very next reading", len(got) == 1, str(wait()))
check("marked as cooldown skipped - on the ticket", trade()["cooldown_skipped"] is True)
check("...and in what the page receives", book.public("NIFTY")["ticket"]["cooldown_skipped"] is True)
with open(LOG, newline="") as f:
    rows = list(csv.DictReader(f))
check("...and in the trade log", rows[-1]["event"] == "OPEN" and rows[-1]["cooldown_skipped"] == "yes",
      f"{rows[-1]['event']} / {rows[-1]['cooldown_skipped']!r}")
check("the first ticket's log row is not marked", rows[0]["cooldown_skipped"] == "")

print("4. ONE SHOT ONLY")
check("stopped out again", stop_out())
check("the next cooldown holds again - the skip does not carry over",
      not feed(rec(), n=60) and wait() == "reentry_cooldown", str(wait()))

print("5. ONLY THE CLOCK IS LIFTED")
ok, _ = book.skip_cooldown("NIFTY"); check("skip accepted", ok)
check("not enough room: still held", not feed(rec(rr=0.5), n=5) and wait() == "reentry_no_room", str(wait()))
check("wide spread: still held", not feed(rec(spread={"pct": 9.0, "bid": 90., "ask": 100.}), n=5)
      and wait() == "wide_spread", str(wait()))
was_brk = getattr(config, "REGIME_OR_REQUIRE_BREAK", True)
config.REGIME_OR_REQUIRE_BREAK = True
check("inside the opening range while a break is required: still held",
      not feed(rec(spot=24350.0), n=5) and wait() in ("or_break",), str(wait()))
config.REGIME_OR_REQUIRE_BREAK = False
check("with only the wait required, the same reading is taken - the skipped ticket comes",
      len(feed(rec(spot=24350.0))) == 1 and trade()["cooldown_skipped"] is True, str(wait()))
config.REGIME_OR_REQUIRE_BREAK = was_brk

print("6. A COOLDOWN THAT RUNS OUT ON ITS OWN IS NOT MARKED")
check("stopped out", stop_out())
feed(rec(), n=5)
check("held", wait() == "reentry_cooldown")
check("20 minutes later it opens by itself", len(feed(rec(), seconds=60, n=21)) == 1, str(wait()))
check("and is not marked skipped", trade()["cooldown_skipped"] is False)

print("7. THE BETWEEN-TICKETS GAP, WHEN ONE IS SET")
was = config.MIN_MINUTES_BETWEEN_TICKETS
config.MIN_MINUTES_BETWEEN_TICKETS = 20
try:
    check("stopped out", stop_out())
    # A flip is not a same-direction re-entry, so only the gap can hold it -
    # and the last ticket opened seconds ago, well inside 20 minutes.
    got = feed(rec("PE"), n=125)                  # a flip, fully confirmed
    check("a flip within 20 min of the last ticket is held by the gap", not got and wait() == "ticket_gap", str(wait()))
    ok, msg = book.skip_cooldown("NIFTY"); check("skip accepted for the gap", ok, msg)
    check("the PE comes and is marked", len(feed(rec("PE"))) == 1 and trade()["cooldown_skipped"] is True, str(wait()))
finally:
    config.MIN_MINUTES_BETWEEN_TICKETS = was

print()
print("COOLDOWN SKIP TEST PASSED" if not fails else f"COOLDOWN SKIP TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
