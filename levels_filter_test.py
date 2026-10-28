#!/usr/bin/env python3
"""The levels check (levels.py, config.LEVELS_CHOICES; the user, 28 Oct 2026: "yes build it"): off by default; when
switched on for an index, a ticket waits while yesterday's high, low or a floor pivot lies closer than 1.5x its risk
in its way - never for the big candle - and the study reads the same levels. Real TicketBook, pinned clock; no
network."""
import datetime as dt
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pandas as pd
import config
import levels
import tickets
import trade_log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = dt.datetime(2026, 10, 29, 11, 0, 0, tzinfo=IST)
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


# yesterday: high 24600, low 24300, close 24500 -> P 24466.67, R1 24633.33, S1 24333.33, R2 24766.67, S2 24166.67
idx = pd.DatetimeIndex(["2026-10-28 09:15", "2026-10-28 15:15", "2026-10-29 09:15", "2026-10-29 10:45"], tz="Asia/Kolkata")
CANDLES = pd.DataFrame({"Open": [24400.0, 24450.0, 24510.0, 24540.0], "High": [24600.0, 24520.0, 24570.0, 24580.0],
                        "Low": [24300.0, 24400.0, 24500.0, 24530.0], "Close": [24450.0, 24500.0, 24540.0, 24560.0]},
                       index=idx)


def rec(side="CE", spot=24560.0, stop=24500.0):
    ce = side == "CE"
    r = abs(spot - stop)
    return {"index": "NIFTY", "bias": "BULLISH" if ce else "BEARISH", "option_type": side,
            "suggested_strike": 24550 if ce else 24550, "spot": spot,
            "index_targets": [spot + r * m if ce else spot - r * m for m in (1, 2, 3)], "index_stop_loss": stop,
            "premium_targets": [150., 170., 190.], "premium_stop_loss": 100., "premium_source": None, "live_ltp": None,
            "option_chain": None, "risk_points": r, "reach_points": 3 * r, "reach_to_risk": 3.0,
            "opening_range": {"ready": True, "high": 24580.0, "low": 24500.0}, "spread": None,
            "technical": {"adx": 30}, "candles": CANDLES}


def book(path=None):
    return tickets.TicketBook(owner=None, market="nse_index", path=path or os.path.join(tempfile.mkdtemp(), "t.csv"))


print("1. OFF BY DEFAULT")
check("config: off, 1.5x room", config.LEVELS_CHOICES == ("off", "on") and config.LEVELS_DEFAULT == "off"
      and config.LEVELS_ROOM_R == 1.5)
b = book()
check("every index starts off", [b.levels_for(k) for k in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")] == ["off"] * 4)
check("the page gets it", b.session()["levels_by"]["SENSEX"] == "off" and b.session()["levels_choices"] == ["off", "on"]
      and b.session()["levels_room_r"] == 1.5)
path = os.path.join(tempfile.mkdtemp(), "t.csv")
b = book(path)
b.configure(levels="on", levels_index="NIFTY")
b.configure(levels="maybe", levels_index="SENSEX")
check("switched on per index, saved, an unknown choice ignored", b.expiry_for("NIFTY") == "nearest"
      and book(path).levels_for("NIFTY") == "on" and book(path).levels_for("SENSEX") == "off")

print("2. THE LEVELS")
named = levels.named_levels(CANDLES)[dt.date(2026, 10, 29)]
check("yesterday's high, low and pivots - not today's", named["H"] == 24600.0 and named["L"] == 24300.0
      and abs(named["P"] - 24466.667) < 0.01 and abs(named["R1"] - 24633.333) < 0.01)
got = levels.blocking(CANDLES, 24560.0, 24500.0, True, 1.5)
check("a call 40 under yesterday's high with 60 risk (needs 90): blocked, named", got and got[0] == "yesterday's high"
      and got[1] == 24600.0 and abs(got[2] - 40) < 1e-6 and got[3] == 60.0, got)
check("the same call with 20 risk (needs 30): room", levels.blocking(CANDLES, 24560.0, 24540.0, True, 1.5) is None)
check("a put 93 above the pivot with 60 risk (needs 90): room", levels.blocking(CANDLES, 24560.0, 24620.0, False, 1.5) is None)
check("a put 60 above the pivot with 50 risk (needs 75): blocked", (levels.blocking(CANDLES, 24527.0, 24577.0, False, 1.5)
                                                                         or ("",))[0] == "the pivot")
check("no yesterday, no candles, no risk: never blocks", levels.blocking(CANDLES.iloc[:2], 24560.0, 24500.0, True, 1.5) is None
      and levels.blocking(None, 24560.0, 24500.0, True, 1.5) is None
      and levels.blocking(CANDLES, 24560.0, 24560.0, True, 1.5) is None)

print("3. THE HOLD")
b = book()
check("off: no hold", b._levels_hold(rec()) is None)
b.configure(levels="on", levels_index="NIFTY")
h = b._levels_hold(rec())
check("on, a level too close: held, saying which and how far", h and h[0] == "levels" and h[1] == "LEVEL CLOSE"
      and "Yesterday's high at 24,600" in h[2] and "40 points above" in h[2], h)
check("on, room enough: no hold", b._levels_hold(rec(stop=24540.0)) is None)
src = open(os.path.join(HERE, "tickets.py")).read()
check("all three open paths ask it; the big candle never", 'self._levels_hold(rec) if info.get("system") != "big_candle" else None'
      in src and "held = self._levels_hold(rec)" in src and "or self._levels_hold(rec) is not None" in src)

print("4. THROUGH THE REAL GATES")
def feed(b, r, n):
    evs = []
    for _ in range(n):
        CLOCK["s"] += 1
        evs += b.update("NIFTY", dict(r))
    return evs
b = book()
b.configure(levels="on", levels_index="NIFTY")
evs = feed(b, rec(), config.SIGNAL_CONFIRM_SECONDS + config.SIGNAL_CONFIRM_TICKS + 5)
check("on, a level in the way: no ticket, LEVEL CLOSE", not any(e.get("kind") == "opened" for e in evs)
      and (b.books["NIFTY"].wait_reason or ("",))[0] == "levels", b.books["NIFTY"].wait_reason)
b2 = book()
evs = feed(b2, rec(), config.SIGNAL_CONFIRM_SECONDS + config.SIGNAL_CONFIRM_TICKS + 5)
check("off, the same reading: the ticket opens", any(e.get("kind") == "opened" for e in evs),
      b2.books["NIFTY"].wait_reason)

print("5. THE STUDY READS THE SAME LEVELS")
check("sr_study takes day_levels / next_level from levels.py",
      "from levels import day_levels, next_level" in open(os.path.join(HERE, "sr_study.py")).read())

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
