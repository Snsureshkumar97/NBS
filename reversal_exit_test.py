#!/usr/bin/env python3
"""A sustained reversal can end a rule ticket early - EARLY_EXIT_ON_REVERSAL (config.py).

The user (27 Sep 2026): "what if we give the signal tool also its own exit time like AI trades" -
the AI desk re-asks itself on every 15-minute candle close and can leave on its own judgement; the
rule tool otherwise never reconsiders an open ticket between its entry and its frozen target/stop,
however long it runs against the signal.

This is NOT "close on the first flip" - that was CLOSE_ON_SIGNAL_FLIP, removed for costing a trade on
every flicker (see its own comment: dozens of times a day, -0.09R after costs). The OPPOSITE direction
has to hold for exactly the same SIGNAL_CONFIRM_SECONDS / SIGNAL_CONFIRM_TICKS a fresh entry itself
needs before this fires - the identical clock, just read against a position - and firing it resets
that clock, so the next ticket, EITHER direction, has to earn its own confirmation from scratch: the
one thing that would reopen the churn by another door is skipped.

Off by default (a strategy change, not yet run through pro_study.py / regime_study.py). Runs the real
TicketBook against a pinned clock, like cooldown_skip_test.py.
"""
import datetime as dt, os, sys, tempfile, types
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

NEXT = {"CE": 24500, "PE": 24200}
def rec(side="CE", spot=None, rr=2.67):
    # Levels derive from `spot`, not fixed numbers, so a trade opened at whichever spot a section
    # picks - including one that re-arms after a close, mid-test - always has a stop and targets
    # that make sense next to it, the way build_recommendation() would actually price them.
    ce = side == "CE"
    spot = spot if spot is not None else (24500.0 if ce else 24200.0)
    step = (60., 110., 160.)
    return {"index": "NIFTY", "bias": "BULLISH" if ce else "BEARISH", "option_type": side,
            "suggested_strike": NEXT[side], "spot": spot,
            "index_targets": [spot + s for s in step] if ce else [spot - s for s in step],
            "index_stop_loss": spot - 60. if ce else spot + 60.,
            "premium_targets": [130., 150., 170.], "premium_stop_loss": 80.,
            "premium_source": None, "live_ltp": None, "option_chain": None,
            "risk_points": 60.0, "reach_points": 160.0, "reach_to_risk": rr,
            "opening_range": {"ready": True, "high": 24400.0, "low": 24300.0},
            "spread": None, "technical": {"adx": 30}}

def new_book():
    b = tickets.TicketBook(owner=None, market="nse_index")
    b.auto_rearm = True
    return b
book = new_book()
def feed(b, r, seconds=1, n=1):
    all_ev = []
    for _ in range(n):
        CLOCK["s"] += seconds
        ev = b.update("NIFTY", r)
        all_ev += ev
        for e in ev:
            if e.get("kind") == "opened":
                NEXT[e["trade"]["option_type"]] += 50   # so the next ticket in this direction never repeats a strike
    return all_ev
def open_one(b):
    feed(b, rec(), n=config.SIGNAL_CONFIRM_SECONDS + 5)
    return b.books["NIFTY"].trade

print("1. SWITCHED ON 27 Sep 2026 - KEPT by reversal_exit_study.py (better in both periods; config.py has the numbers)")
check("EARLY_EXIT_ON_REVERSAL is True in the shipped config", config.EARLY_EXIT_ON_REVERSAL is True)

_saved = config.EARLY_EXIT_ON_REVERSAL
config.EARLY_EXIT_ON_REVERSAL = False
try:
    print("2. WITH IT OFF, EVEN A LONG SUSTAINED REVERSAL CHANGES NOTHING (the switch genuinely gates this)")
    book = new_book()
    t = open_one(book)
    check("a first ticket opens", t is not None and t["status"] == "OPEN")
    feed(book, rec("PE", spot=24500.0), n=config.SIGNAL_CONFIRM_SECONDS + 60)   # the opposite side, held far longer than needed - spot 24500 touches neither the frozen stop (24440) nor T1 (24560)
    check("...and is still open: nothing closes it without the switch", book.books["NIFTY"].trade["status"] == "OPEN")
finally:
    config.EARLY_EXIT_ON_REVERSAL = _saved

config.EARLY_EXIT_ON_REVERSAL = True
try:
    print("3. A BRIEF REVERSAL - LESS THAN THE CONFIRMATION WINDOW - CHANGES NOTHING")
    book = new_book()
    t = open_one(book)
    CLOCK["s"] += 1
    feed(book, rec("PE", spot=24500.0), n=config.SIGNAL_CONFIRM_SECONDS - 20)     # short of the window
    check("still open: a flicker is not a sustained reversal", book.books["NIFTY"].trade["status"] == "OPEN")
    feed(book, rec(), n=3)                                                       # and it comes back
    check("...and coming back to the original side leaves it open, exactly as before", book.books["NIFTY"].trade["status"] == "OPEN")

    print("3b. ENOUGH TIME HAS PASSED BUT NOT ENOUGH READINGS - STILL NO REVERSAL")
    # The same two-part gate a fresh entry itself has to pass: TIME held (SIGNAL_CONFIRM_SECONDS) AND enough
    # independent READINGS (SIGNAL_CONFIRM_TICKS) - so a signal recomputed less often than once a second could
    # not satisfy the clock alone on a couple of stale-feeling readings. Spread few readings far apart in time.
    book = new_book()
    t = open_one(book)
    for _ in range(config.SIGNAL_CONFIRM_TICKS - 1):
        CLOCK["s"] += config.SIGNAL_CONFIRM_SECONDS      # time alone is already well past the window each time
        book.update("NIFTY", rec("PE", spot=24500.0))
    check(f"only {config.SIGNAL_CONFIRM_TICKS - 1} PE readings so far, time easily past {config.SIGNAL_CONFIRM_SECONDS}s: still open",
          book.books["NIFTY"].trade["status"] == "OPEN")
    CLOCK["s"] += 1
    book.update("NIFTY", rec("PE", spot=24500.0))
    check(f"the {config.SIGNAL_CONFIRM_TICKS}th reading meets both parts of the gate: now it closes",
          book.books["NIFTY"].trade["status"] != "OPEN")

    print("4. A SUSTAINED REVERSAL CLOSES THE TICKET")
    book = new_book()
    t = open_one(book)
    tid = t["trade_id"]
    fired = []
    book.listeners.append(lambda kind, trade: fired.append((kind, trade.get("trade_id"))) if kind == "closed" else None)
    evs = feed(book, rec("PE", spot=24500.0), n=config.SIGNAL_CONFIRM_SECONDS + config.SIGNAL_CONFIRM_TICKS + 2)
    closed = book.books["NIFTY"].trade
    check("the ticket is closed, and says why in its own words", closed["status"] != "OPEN" and "reversed" in closed["status"].lower(), closed["status"])
    check("the CLOSE event is in what update() returned", any(e.get("kind") == "closed" and e["trade"]["strike"] == t["strike"] for e in evs), evs)
    close_ev = next(e for e in evs if e.get("kind") == "closed")
    check("...at the current price (the level the reversal was read at), not the trade's own frozen levels", close_ev["trade"]["exit"] == 24500.0, close_ev["trade"])
    check("real orders hear about it too - the SAME listener path a target or a stop already use", ("closed", tid) in fired, fired)
    import csv
    rows = list(csv.DictReader(open(LOG)))
    close_row = [r for r in rows if r["trade_id"] == tid and r["event"] == "CLOSE"][0]
    check("it is written to the trade log with a status of its own", "reversed" in close_row["status"].lower())
    check("...and it is neither a target hit nor a stop-out, in the daily stats' own words - it is its own kind of ending",
          not trade_log.is_target_close(close_row["status"]) and "stop-loss" not in close_row["status"].lower())

    print("5. IT DOES NOT HAND THE DOOR STRAIGHT BACK OPEN")
    check("no ticket reopened in the SAME batch of readings, even though the new (PE) direction technically just held long enough to pass a fresh entry's own gate",
          not any(e.get("kind") == "opened" for e in evs), evs)
    check("the very next reading reads CONFIRMING, not a live PE ticket", (book.public("NIFTY")["wait"] or {}).get("code") == "confirming",
          book.public("NIFTY")["wait"])
    feed(book, rec("PE", spot=24500.0), n=5)
    check("...and a few more readings the same way STILL do not reopen it - the clock really did reset", book.books["NIFTY"].trade["status"] != "OPEN"
          and (book.public("NIFTY")["wait"] or {}).get("code") == "confirming")
    evs2 = feed(book, rec("PE", spot=24500.0), n=config.SIGNAL_CONFIRM_SECONDS + config.SIGNAL_CONFIRM_TICKS + 2)
    check("only once the FULL confirmation window has run again does a new ticket open", any(e.get("kind") == "opened" for e in evs2), evs2)
    check("...and it is the new (PE) direction", book.books["NIFTY"].trade["option_type"] == "PE")

    print("6. A NEUTRAL READING IS NOT A REVERSAL")
    book = new_book()
    t = open_one(book)
    neutral = dict(rec(), bias="NEUTRAL", option_type=None)
    feed(book, neutral, n=config.SIGNAL_CONFIRM_SECONDS + config.SIGNAL_CONFIRM_TICKS + 5)
    check("no opinion is not held against an open trade", book.books["NIFTY"].trade["status"] == "OPEN")

    print("7. TARGET / STOP STILL TAKE PRIORITY WHEN BOTH ARE TRUE ON THE SAME READING")
    book = new_book()
    t = open_one(book)
    # First sustain the reversal (PE) at a price that touches neither the CE trade's frozen stop (24440) nor
    # T1 (24560) - one tick SHORT of the window, so it has not fired yet. Then, on the single NEXT tick, the
    # price also crosses T2 (24610, the exit target) while still reading PE: both conditions become true for
    # the first time on the very same reading. Checked once, right there - not fed on any further tick.
    feed(book, rec("PE", spot=24500.0), n=max(config.SIGNAL_CONFIRM_SECONDS, config.SIGNAL_CONFIRM_TICKS) - 1)
    check("the reversal has not fired yet - one tick short of the window", book.books["NIFTY"].trade["status"] == "OPEN")
    CLOCK["s"] += 1
    evs = book.update("NIFTY", rec("PE", spot=24620.0))
    closed = book.books["NIFTY"].trade
    check("the close is the target hit, not the reversal - the two never race for the same tick",
          "hit" in closed["status"].lower() and "reversed" not in closed["status"].lower(), closed["status"])

    print("8. NEVER FOR A BOOK WHOSE TICKETS ARE ONLY EVER track()ED - THE AI DESK'S OWN PATTERN")
    aib = new_book()
    r0 = rec()
    aib.books["NIFTY"] = tickets.IndexBook("NIFTY")
    aib._open(aib.books["NIFTY"], r0)                 # opened directly, the way ai_desk.py's book._open() is - never through update()
    for _ in range(config.SIGNAL_CONFIRM_SECONDS + config.SIGNAL_CONFIRM_TICKS + 30):
        CLOCK["s"] += 1
        aib.track("NIFTY", rec("PE", spot=24500.0))   # the AI desk's own path - never update(), never _consider() (24500: neutral, touches neither the frozen stop nor T1)
    check("a book whose tickets are only ever track()ed is never touched by this - its confirm_dir stays unset",
          aib.books["NIFTY"].confirm_dir is None and aib.books["NIFTY"].trade["status"] == "OPEN")
finally:
    config.EARLY_EXIT_ON_REVERSAL = _saved

print("REVERSAL EXIT TEST PASSED" if not fails else f"REVERSAL EXIT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
