#!/usr/bin/env python3
"""A strike already traded today is not ticketed again - the tool moves to the next.

Asked for by the user on 23 Sep 2026, after NIFTY 23450 CE was ticketed twice in
one day: a second buy on a strike already held averages into the first in a real
account, and one already round-tripped shows as a single blended line. "The tool
can pick next possible strike" - so:

  * signal_engine names the NEXT free strike itself (away from the money first,
    then back through it, up to STRIKE_SEARCH listed strikes) and computes the
    premium, targets, stop and spread for THAT contract;
  * with none free, it says so and no ticket is issued;
  * the ticket engine holds any reading that still names a taken strike (a stale
    one) and stops auto re-arm reopening the strike it just closed;
  * the AI desk and the rule tickets share one account, so each sees the other's
    strikes - the desk is told, and its own validation refuses;
  * the Signal page says which strike was skipped and why.

Real engines on temporary disks, a pinned clock and fake feeds. Nothing here can
reach an account.
"""
import csv
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

import ai_desk as ad
import config
import feeds
import signal_engine as SE
import tickets
import trade_log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = dt.datetime(2026, 9, 22, 11, 0, 0, tzinfo=IST)          # a Tuesday, mid-session
CLOCK = {"s": 0.0}
tickets.now_ist = lambda: BASE + dt.timedelta(seconds=CLOCK["s"])
tickets.time = types.SimpleNamespace(time=lambda: 1_780_000_000 + CLOCK["s"])
tickets.is_market_open = lambda *a, **k: True
tickets._safe_explain = lambda rec: {"verdict": "test"}

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def chain(atm=25000, n=8, step=50, dead=(), avail=True):
    strikes = []
    for k in range(-n, n + 1):
        s = float(atm + k * step)
        strikes.append({"strike": s, "call_oi": 90000, "put_oi": 90000,
                        "call_ltp": 0 if s in dead else max(1.0, 160 - k * 14),
                        "put_ltp": 0 if s in dead else max(1.0, 160 + k * 14)})
    return {"available": avail, "strikes": strikes}


print("1. THE NEXT FREE STRIKE, ON ITS OWN")
oi = chain()
nf = SE._next_free_strike
check("nothing traded: the strike itself, not moved", nf(oi, 25000, "CE", set()) == (25000, False))
check("a different SIDE traded on it does not count",
      nf(oi, 25000, "CE", {(25000.0, "PE")}) == (25000, False))
check("a call moves UP one strike, away from the money", nf(oi, 25000, "CE", {(25000.0, "CE")}) == (25050.0, True))
check("a put moves DOWN one strike, away from the money", nf(oi, 25000, "PE", {(25000.0, "PE")}) == (24950.0, True))
check("away taken too: it goes back through the money instead",
      nf(oi, 25000, "CE", {(25000.0, "CE"), (25050.0, "CE")}) == (24950.0, True))
check("...and on, one listed strike at a time",
      nf(oi, 25000, "CE", {(25000.0, "CE"), (25050.0, "CE"), (24950.0, "CE")}) == (25100.0, True))
allx = {(25000.0 + k * 50, "CE") for k in range(-3, 4)}
check("every strike within the search taken: None, and it says taken", nf(oi, 25000, "CE", allx) == (None, True))
check("a neighbour with no live premium is skipped, not offered",
      nf(chain(dead=(25050.0,)), 25000, "CE", {(25000.0, "CE")}) == (24950.0, True))
check("no chain at all: nothing to look at, so nothing changes",
      nf({"available": False, "strikes": []}, 25000, "CE", {(25000.0, "CE")}) == (25000, False))
check("a chain marked unavailable is not looked into",
      nf(chain(avail=False), 25000, "CE", {(25000.0, "CE")}) == (25000, False))
uneven = {"available": True, "strikes": [{"strike": float(k), "call_ltp": 50.0, "put_ltp": 50.0}
                                          for k in (60000, 60200, 60600, 61000, 61400)]}
check("uneven Delta gaps: by position in the list, not by a fixed step",
      nf(uneven, 60600, "CE", {(60600.0, "CE")}) == (61000.0, True))
check("...and the other way for a put", nf(uneven, 60600, "PE", {(60600.0, "PE")}) == (60200.0, True))
check("at the edge of the list it uses the side that exists",
      nf(uneven, 61400, "CE", {(61400.0, "CE")}) == (61000.0, True))


print("2. THE RECOMMENDATION NAMES THE NEXT STRIKE, AND ITS PREMIUM IS THAT STRIKE'S")
step = config.INSTRUMENTS["NIFTY"]["strike_step"]


def series(sign, seed=5):
    rs = np.random.RandomState(seed)
    n = 220
    idx = pd.date_range(end="2026-08-19 15:15", periods=n, freq="15min")
    drift = np.full(n, sign * 2.4)
    drift[-10:] = sign * 2.4 * 3            # the recent pace picks up, so momentum agrees with the trend
    close = 24000 + np.cumsum(rs.randn(n) * 6 + drift)
    op = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"Open": op, "High": np.maximum(op, close) + 5, "Low": np.minimum(op, close) - 5,
                         "Close": close, "Volume": 1000}, index=idx)


def reading(sign, avoid=None, dead=()):
    df = series(sign)
    tech = SE.compute_technical_signal(df)
    spot = float(df["Close"].iloc[-1])
    atm = round(spot / step) * step
    strikes = [{"strike": float(atm + k * step), "call_oi": 90000 - abs(k) * 900, "put_oi": 90000 - abs(k) * 900,
                "call_ltp": 0 if (atm + k * step) in dead else max(1.0, 160 - k * 14),
                "put_ltp": 0 if (atm + k * step) in dead else max(1.0, 160 + k * 14)} for k in range(-8, 9)]
    ch = {"expiry": "2026-08-27", "spot": spot, "pcr": 1.3 if sign > 0 else 0.7, "max_pain": atm,
          "top_call_oi_strike": atm + 200, "top_put_oi_strike": atm - 200, "strikes": strikes}
    oi_ = SE.compute_option_chain_signal(ch)
    reach = SE.compute_reachability(tech["last_close"], oi_, df, dt.datetime(2026, 8, 19, 11, 0), adx=tech.get("adx"))
    return SE.build_recommendation("NIFTY", tech, oi_, step, reach=reach, avoid_strikes=avoid), strikes


base, _ = reading(+1)
check("baseline: a live bullish call reading to swap", base["bias"] == "BULLISH" and base["option_type"] == "CE"
      and base["live_ltp"] is not None, (base["bias"], base["option_type"], base["live_ltp"]))
k0 = float(base["suggested_strike"])
check("with nothing traded there is no swap and nothing is taken",
      base["strike_swap"] is None and base["strike_taken"] is False)
none_arg, _ = reading(+1, avoid=set())
check("an empty set of taken strikes changes nothing at all",
      none_arg["suggested_strike"] == base["suggested_strike"] and none_arg["strike_swap"] is None)

swap, strikes = reading(+1, avoid={(k0, "CE")})
by = {s["strike"]: s for s in strikes}
check("the money strike traded: the next call strike up is named", float(swap["suggested_strike"]) == k0 + step,
      swap["suggested_strike"])
check("...and the swap says from which to which", swap["strike_swap"] == {"from": k0, "to": k0 + step}, swap["strike_swap"])
check("its premium is THAT strike's own, not the old one's", swap["live_ltp"] == by[k0 + step]["call_ltp"] != base["live_ltp"],
      (swap["live_ltp"], base["live_ltp"]))
check("so the premium targets and stop were worked out from it, not carried over",
      swap["premium_targets"] != base["premium_targets"] and swap["premium_stop_loss"] != base["premium_stop_loss"],
      (swap["premium_targets"], base["premium_targets"]))
check("still bullish and still a ticket-worthy reading", swap["bias"] == "BULLISH" and swap["strike_taken"] is False)
check("the at-the-money strike it reports is unchanged (it is the market's, not the ticket's)",
      swap["atm_strike"] == base["atm_strike"])

both, _ = reading(+1, avoid={(k0, "CE"), (k0 + step, "CE")})
check("the neighbour up taken as well: it comes back through the money", float(both["suggested_strike"]) == k0 - step,
      both["suggested_strike"])
wrong_side, _ = reading(+1, avoid={(k0, "PE")})
check("a PUT traded on that strike does not move a call", float(wrong_side["suggested_strike"]) == k0
      and wrong_side["strike_swap"] is None)

boxed, _ = reading(+1, avoid={(k0 + j * step, "CE") for j in range(-3, 4)})
check("every strike near the money taken: no strike is offered and it says so",
      boxed["strike_taken"] is True and boxed["strike_swap"] is None, (boxed["strike_taken"], boxed["strike_swap"]))
deadn, _ = reading(+1, avoid={(k0, "CE")}, dead=(k0 + step,))
check("a neighbour with no live premium is not the one named", float(deadn["suggested_strike"]) == k0 - step,
      deadn["suggested_strike"])

bear, _ = reading(-1)
if bear["bias"] == "BEARISH" and bear["option_type"] == "PE" and bear["live_ltp"] is not None:
    kp = float(bear["suggested_strike"])
    bswap, _ = reading(-1, avoid={(kp, "PE")})
    check("a put moves DOWN a strike in a real reading too", float(bswap["suggested_strike"]) == kp - step
          and bswap["strike_swap"] == {"from": kp, "to": kp - step}, bswap["strike_swap"])
else:
    check("a real bearish reading to swap (the fixture's)", False, (bear["bias"], bear["option_type"]))

neutral = SE.build_recommendation("NIFTY", SE.compute_technical_signal(series(0, seed=3)),
                                  SE.compute_option_chain_signal(None), step, avoid_strikes={(24500.0, "CE")})
check("no signal, no chain: nothing to swap and nothing claimed",
      neutral["strike_swap"] is None and neutral["strike_taken"] is False)


print("2b. THE REST PASS (main.fetch_recommendation) HANDS THE STRIKES ON")
import main
main.now_ist = lambda: dt.datetime(2026, 8, 19, 11, 0)          # the reachability check reads the clock


def session_series(sign, seed=5):
    """Ten sessions of 09:15-15:15 bars: fetch_recommendation drops anything outside them."""
    rs = np.random.RandomState(seed)
    days = pd.bdate_range(end="2026-08-19", periods=10)
    idx = pd.DatetimeIndex([d + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=15 * i)
                            for d in days for i in range(25)])
    n = len(idx)
    drift = np.full(n, sign * 2.4)
    drift[-10:] = sign * 2.4 * 3
    close = 24000 + np.cumsum(rs.randn(n) * 6 + drift)
    op = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"Open": op, "High": np.maximum(op, close) + 5, "Low": np.minimum(op, close) - 5,
                         "Close": close, "Volume": 1000}, index=idx)


class FakeProvider:
    """Just what fetch_recommendation asks a data provider for."""
    def get_ohlc(self, index_key, interval=None, lookback_days=None):
        return session_series(+1)
    def get_option_chain(self, index_key, expiry=None):
        df = session_series(+1)
        spot = float(df["Close"].iloc[-1])
        atm = round(spot / step) * step
        return {"expiry": "2026-08-27", "spot": spot, "pcr": 1.3, "max_pain": atm,
                "top_call_oi_strike": atm + 200, "top_put_oi_strike": atm - 200,
                "strikes": [{"strike": float(atm + k * step), "call_oi": 90000 - abs(k) * 900,
                             "put_oi": 90000 - abs(k) * 900, "call_ltp": max(1.0, 160 - k * 14),
                             "put_ltp": max(1.0, 160 + k * 14)} for k in range(-8, 9)]}


plain, _n = main.fetch_recommendation(FakeProvider(), "NIFTY", "15m", 5, quiet=True)
check("the rest pass, with nothing traded, names the money strike as before",
      plain["option_type"] == "CE" and plain["strike_swap"] is None, (plain["option_type"], plain["strike_swap"]))
kr = float(plain["suggested_strike"])
moved, _n = main.fetch_recommendation(FakeProvider(), "NIFTY", "15m", 5, quiet=True, avoid_strikes={(kr, "CE")})
check("...and with it traded, names the next one - the strikes really do reach build_recommendation",
      float(moved["suggested_strike"]) == kr + step and moved["strike_swap"] == {"from": kr, "to": kr + step},
      (moved["suggested_strike"], moved["strike_swap"]))


print("3. THE TICKET ENGINE HOLDS A READING THAT STILL NAMES A TAKEN STRIKE")
LOG = os.path.join(tempfile.mkdtemp(), "trades.csv")
NEXT = {"CE": 24500, "PE": 24200}


def rec(side="CE", strike=None, spot=None, swap=None, taken=False):
    ce = side == "CE"
    return {"index": "NIFTY", "bias": "BULLISH" if ce else "BEARISH", "option_type": side,
            "suggested_strike": strike if strike is not None else NEXT[side],
            "spot": spot if spot is not None else (24500.0 if ce else 24200.0),
            "index_targets": [24560., 24610., 24660.] if ce else [24140., 24090., 24040.],
            "index_stop_loss": 24440. if ce else 24260.,
            "premium_targets": [130., 150., 170.], "premium_stop_loss": 80.,
            "premium_source": None, "live_ltp": None, "option_chain": None,
            "risk_points": 60.0, "reach_points": 160.0, "reach_to_risk": 2.67,
            "opening_range": {"ready": True, "high": 24400.0, "low": 24300.0},
            "spread": None, "technical": {"adx": 30},
            "strike_swap": swap, "strike_taken": taken}


def new_book(path=LOG):
    b = tickets.TicketBook(owner=None, market="nse_index", path=path)
    b.auto_rearm = False
    return b


book = new_book()
def feed(b, r, seconds=1, n=1):
    opened = []
    for _ in range(n):
        CLOCK["s"] += seconds
        opened += [e for e in b.update("NIFTY", r) if e.get("kind") == "opened"]
    return opened
wait = lambda b: (b.books["NIFTY"].wait_reason or (None, None))[0]
trade = lambda b: b.books["NIFTY"].trade
def stop_out(b):
    feed(b, rec(spot=24430.0))
    return trade(b) is None or trade(b)["status"] != "OPEN"


check("nothing is taken before the first ticket", book.contracts_today() == set())
got = feed(book, rec(strike=24500), n=125)
check("the first ticket opens on 24500 CE", len(got) == 1 and trade(book)["strike"] == 24500, wait(book))
check("...and the book knows it at once, without waiting for its cache to expire",
      book.contracts_today() == {("NIFTY", 24500.0, "CE")}, book.contracts_today())
check("the ticket carries no swap when none was made", trade(book)["strike_swap"] is None)
check("stopped out", stop_out(book))
book.skip_cooldown("NIFTY")
check("the same strike again is held, and the page is told why",
      not feed(book, rec(strike=24500), n=5) and wait(book) == "strike_taken"
      and (book.public("NIFTY")["wait"] or {}).get("badge") == "STRIKE TAKEN",
      (wait(book), book.public("NIFTY")["wait"]))
check("the held reason names the strike", "24500" in (book.public("NIFTY")["wait"] or {}).get("why", ""))
got = feed(book, rec(strike=24550, swap={"from": 24500.0, "to": 24550.0}))
check("the next strike, named by the signal, opens", len(got) == 1 and trade(book)["strike"] == 24550, wait(book))
check("the swap is kept on the ticket, so the page can say why it is on that strike",
      trade(book)["strike_swap"] == {"from": 24500.0, "to": 24550.0})
check("...and it reaches the page", book.public("NIFTY")["ticket"]["strike_swap"] == {"from": 24500.0, "to": 24550.0})
with open(LOG, newline="") as fh:
    opens = [r for r in csv.DictReader(fh) if r["event"] == "OPEN"]
check("the log has one OPEN per strike, never two on the same", [r["strike"] for r in opens] == ["24500", "24550"],
      [r["strike"] for r in opens])

check("stopped out again", stop_out(book))
book.skip_cooldown("NIFTY")
check("a reading that says no strike is free holds, in its own words",
      not feed(book, rec(strike=24600, taken=True), n=5) and wait(book) == "strike_taken"
      and (book.public("NIFTY")["wait"] or {}).get("badge") == "NO FREE STRIKE",
      (wait(book), book.public("NIFTY")["wait"]))
check("the other side of the same strike is not held by it",
      book._strike_taken(book.books["NIFTY"], rec("PE", strike=24500)) is None)
check("a fresh strike is not held", book._strike_taken(book.books["NIFTY"], rec(strike=24650)) is None)
check("a reading with a garbled strike is left to the other gates, not crashed on",
      book._strike_taken(book.books["NIFTY"], dict(rec(), suggested_strike=None)) is None)

print("3aa. A TICKET OPENED THIS SECOND IS SPOKEN FOR THIS SECOND")
fresh = new_book(os.path.join(tempfile.mkdtemp(), "trades.csv"))
check("an empty book says so (and remembers having looked)", fresh.contracts_today() == set())
fresh._open(fresh.books["NIFTY"], rec(strike=24700))            # no time passes: the 3s look-aside is still warm
check("the very next look sees the new ticket - the AI desk asks straight after a rule ticket opens",
      fresh.contracts_today() == {("NIFTY", 24700.0, "CE")}, fresh.contracts_today())

print("3a. THE SIDE IS PART OF THE CONTRACT")
LOGP = os.path.join(tempfile.mkdtemp(), "trades.csv")
bp = new_book(LOGP)
feed(bp, rec("PE", strike=24200), n=125)
check("a put ticket is opened on 24200 PE", trade(bp) is not None and trade(bp)["option_type"] == "PE")
check("the book records it as a PUT, not as a call", bp.contracts_today() == {("NIFTY", 24200.0, "PE")}, bp.contracts_today())
check("so a PUT on that strike is held...", bp._strike_taken(bp.books["NIFTY"], rec("PE", strike=24200)) is not None)
check("...and a CALL on it is not", bp._strike_taken(bp.books["NIFTY"], rec("CE", strike=24200)) is None)

print("3b. A RESTART FORGETS NOTHING")
book2 = new_book()
check("a book built again over the same log still knows both strikes",
      book2.contracts_today() == {("NIFTY", 24500.0, "CE"), ("NIFTY", 24550.0, "CE")}, book2.contracts_today())
was_cd = config.REENTRY_COOLDOWN_MIN
config.REENTRY_COOLDOWN_MIN = 0            # so the 20-minute wait is not what holds it
try:
    check("...and holds a taken one, once its confirmation has run again",
          not feed(book2, rec(strike=24550), n=125) and wait(book2) == "strike_taken", wait(book2))
finally:
    config.REENTRY_COOLDOWN_MIN = was_cd

print("3c. TOMORROW IS A NEW DAY")
CLOCK["s"] += 86400
check("the next calendar day starts with none taken", book2.contracts_today() == set(), book2.contracts_today())
CLOCK["s"] -= 86400

print("3d. AUTO RE-ARM CANNOT REOPEN THE STRIKE IT JUST CLOSED")
# A re-arm at the moment of an exit is normally held by the 20-minute same-direction cooldown, which
# would make this test pass whatever the strike guard did. With the cooldown at nothing, the strike is
# the only gate that can stop it - and the control below proves the path really does re-arm.
was_cd = config.REENTRY_COOLDOWN_MIN
config.REENTRY_COOLDOWN_MIN = 0
try:
    b = new_book(os.path.join(tempfile.mkdtemp(), "trades.csv"))
    b.auto_rearm = True
    feed(b, rec(strike=24500), n=125)
    check("a ticket is open on 24500 CE", trade(b) is not None and trade(b)["status"] == "OPEN")
    CLOCK["s"] += 121                                  # past the anti-runaway floor
    evs = b.update("NIFTY", rec(strike=24500, spot=24430.0))
    check("it stops out", trade(b)["status"] != "OPEN" and "stop-loss hit" in trade(b)["status"], trade(b)["status"])
    check("and a reading that still names the strike just traded does NOT re-arm onto it",
          not any(e.get("rearmed") for e in evs) and trade(b)["status"] != "OPEN", evs)
    c = new_book(os.path.join(tempfile.mkdtemp(), "trades.csv"))
    c.auto_rearm = True
    feed(c, rec(strike=24500), n=125)
    CLOCK["s"] += 121
    evs = c.update("NIFTY", rec(strike=24550, spot=24430.0))
    check("control: the same stop-out on a reading naming a free strike DOES re-arm, onto that strike",
          any(e.get("rearmed") for e in evs) and trade(c)["status"] == "OPEN" and trade(c)["strike"] == 24550,
          (evs, trade(c) and (trade(c)["status"], trade(c)["strike"])))
finally:
    config.REENTRY_COOLDOWN_MIN = was_cd


print("4. THE AI DESK AND THE RULE TICKETS SHARE ONE ACCOUNT")
EXP = "2026-09-22"
AT = {"now": dt.datetime(2026, 9, 22, 11, 0, 0, tzinfo=IST), "clock": 1_789_000_000.0}


def chain2(spot=25010.0):
    st = []
    for k in range(24500, 25550, 50):
        c_, p_ = 130 + (25000 - k) * 0.4, 120 - (25000 - k) * 0.4
        st.append({"strike": float(k), "call_ltp": round(max(c_, 1), 2), "put_ltp": round(max(p_, 1), 2),
                   "call_bid": round(max(c_, 1) - 0.25, 2), "call_ask": round(max(c_, 1) + 0.25, 2),
                   "put_bid": round(max(p_, 1) - 0.25, 2), "put_ask": round(max(p_, 1) + 0.25, 2),
                   "call_oi": 1000, "put_oi": 900})
    return {"available": True, "expiry": EXP, "spot": spot, "strikes": st, "pcr": 0.9}


AREC = {"index": "NIFTY", "spot": 25010.0, "bias": "BULLISH", "option_type": "CE", "confidence": "Medium",
        "suggested_strike": 25000, "option_chain": chain2(), "technical": {"adx": 24.0}}


class FakeFeed:
    def __init__(self, email):
        self.market, self.email, self.key = "nse_index", email, f"{email}#nse_index"
        self.lock = threading.RLock()
        self.tickets = tickets.TicketBook(owner=email, market="nse_index")
        self.streamer, self.dstream, self.faults = None, None, []
        self.state = {"feed": "ok", "indices": {n: {"rec": AREC} for n in config.instruments_in("nse_index")}}
    def instruments(self):
        return config.instruments_in("nse_index")
    def snapshot(self):
        return {"indices": {}, "tickets": {}, "session": {}}
    def _note_fault(self, where, text):
        self.faults.append((where, text))


def open_on_rules(f, strike, side="CE"):
    r = dict(AREC, option_type=side, bias="BULLISH" if side == "CE" else "BEARISH", suggested_strike=strike,
             premium_source="live", live_ltp=130.0, premium_targets=[145., 154., 160.], premium_stop_loss=110.0,
             index_targets=[], index_stop_loss=None, reward=None)
    with f.tickets.lock:
        f.tickets._open(f.tickets.books["NIFTY"], r)


def plan(strike, side="CE"):
    return {"side": side, "strike": strike, "target": 160.0, "stop": 110.0, "ltp": 130.0, "expiry": EXP,
            "contract": f"NIFTY|{strike:g}|{side}|{EXP}", "rr": 1.5}


f = FakeFeed("samestrike-a@example.invalid")
d = ad.AIDesk(f, now=lambda: AT["now"], clock=lambda: AT["clock"], start=False)
prop = {"option_type": "CE", "strike": 25000.0, "target": 160.0, "stop": 110.0}
ok_, why, _p = d.validate("NIFTY", AREC, prop)
check("before anything is traded the desk may take 25000 CE", ok_, why)
open_on_rules(f, 25000.0)
tickets.now_ist = lambda: AT["now"]
f.tickets.books["NIFTY"].trade["status"] = "CLOSED — test"      # done; the strike is still traded today
f.tickets._contracts_cache = None
ok_, why, _p = d.validate("NIFTY", AREC, prop)
check("a strike the RULE TICKETS traded today is refused to the desk",
      not ok_ and "already traded today" in why and "rule tickets" in why, why)
ok_, why, _p = d.validate("NIFTY", AREC, dict(prop, strike=25050.0, target=140.0, stop=90.0))   # its premium is 110
check("the next strike is fine", ok_, why)
ok_, why, _p = d.validate("NIFTY", AREC, dict(prop, option_type="PE", strike=25000.0, target=170.0, stop=95.0))  # 120
check("the other side of the same strike is fine", ok_, why)
told = d._desk_info("NIFTY")["contracts_already_traded_today"]
check("the model is told it, so it chooses another strike itself instead of being refused",
      "NIFTY|25000|CE" in told, told)
d2 = ad.AIDesk(FakeFeed("samestrike-b@example.invalid"), now=lambda: AT["now"], clock=lambda: AT["clock"], start=False)
d2.contracts = [f"NIFTY|25200|CE|{EXP}"]              # its saved list, with nothing in any log
ok_, why, _p = d2.validate("NIFTY", AREC, dict(prop, strike=25200.0, target=110.0, stop=70.0))   # premium 90
check("the desk's own saved list refuses on its own, whatever the logs hold", not ok_ and "already traded" in why, why)
d._open("NIFTY", dict(AREC, strike_swap={"from": 25000.0, "to": 25050.0}, strike_taken=True), plan(25100.0), "a test entry")
check("an AI ticket does not inherit the RULE reading's strike swap - the desk named its own strike",
      d._open_trade("NIFTY")["strike_swap"] is None and d._open_trade("NIFTY")["strike"] == 25100,
      d._open_trade("NIFTY")["strike_swap"])
told = d._desk_info("NIFTY")["contracts_already_traded_today"]
check("its own contract keeps the form it always had, with the expiry",
      f"NIFTY|25100|CE|{EXP}" in told and told.count("NIFTY|25100|CE") == 0, told)
check("...and the two lists are joined, each strike once", sorted(t.split("|")[1] for t in told) == ["25000", "25100"], told)
tickets.now_ist = lambda: BASE + dt.timedelta(seconds=CLOCK["s"])

print("4b. AND THE RULES ARE TOLD WHAT THE DESK TRADED")
fake = types.SimpleNamespace(tickets=f.tickets, ai=types.SimpleNamespace(book=d.book), faults=[])
fake._note_fault = lambda where, text: fake.faults.append((where, text))
tickets.now_ist = lambda: AT["now"]
f.tickets._contracts_cache = None
d.book._contracts_cache = None
avoid = feeds.Feed._avoid_strikes(fake, "NIFTY")
check("the feed hands signal_engine the strikes from BOTH books, as (strike, side)",
      avoid == {(25000.0, "CE"), (25100.0, "CE")}, avoid)
check("...only for the index asked about", feeds.Feed._avoid_strikes(fake, "BANKNIFTY") == set())
broken = types.SimpleNamespace(tickets=types.SimpleNamespace(contracts_today=lambda: 1 / 0), ai=None, faults=[])
broken._note_fault = lambda where, text: broken.faults.append((where, text))
check("a failure reading them never breaks the reading - it is noted and the guard behind it still stands",
      feeds.Feed._avoid_strikes(broken, "NIFTY") == set() and len(broken.faults) == 1, broken.faults)
tickets.now_ist = lambda: BASE + dt.timedelta(seconds=CLOCK["s"])

print("5. WHAT THE FEED HANDS THE PAGE, AND WHAT THE PAGE SAYS")
src = open(os.path.join(HERE, "feeds.py")).read()
check("both places a reading is built are given the strikes already traded",
      src.count("avoid_strikes=self._avoid_strikes(name)") == 2, src.count("avoid_strikes=self._avoid_strikes(name)"))
check("the public reading carries the swap and the no-free-strike flag",
      '"strike_swap": rec.get("strike_swap")' in src and '"strike_taken": rec.get("strike_taken")' in src)

NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own ladder", False, "install node to run this section")
else:
    WEB = open(os.path.join(HERE, "web_server.py")).read()
    a = WEB.index("const num=")
    b_ = WEB.index("function unitLabel(")
    c_ = WEB.index("function ladder(r, tk){")
    d_ = WEB.index("function riskBox(")
    h = WEB.index("let CCY = ")
    i = WEB.index("function feedTag(")
    prog = """
const assert = require("assert");
const EL = {};
function mkEl(id){
  return {id, innerHTML: "", textContent: "", title: "", value: "", disabled: false,
          dataset: {}, options: [], style: {}, classList: {toggle(){}},
          add(opt){ this.options.push(opt); }};
}
function $(id){ return EL[id] || (EL[id] = mkEl(id)); }
class Option { constructor(text, value){ this.text = text; this.value = value; } }
let LAST = {market_open: true}, CUR = "NIFTY";
""" + WEB[h:i] + WEB[a:b_] + WEB[b_:c_] + WEB[c_:d_] + r'''
const rec = {ltp: 130.0, premium_targets: [145.0, 154.0, 160.0], premium_stop: 110.0,
             strike: 25050, option_type: "CE", exit_at: "T2", lot_size: 65, premium_source: "live",
             targets: [], odds: {}, strike_swap: {from: 25000, to: 25050}, strike_taken: false};
ladder(rec, {open: false});
let n = $("lnote").innerHTML;
assert.ok(n.includes("25050") && n.includes("rather than") && n.includes("25000") && n.includes("already traded today"),
          "a swapped reading says which strike it moved to, from which, and why - " + n);

ladder(Object.assign({}, rec, {strike_swap: null}), {open: false});
assert.ok(!$("lnote").innerHTML.includes("already traded"), "no swap: nothing said about one - " + $("lnote").innerHTML);

ladder(Object.assign({}, rec, {strike_swap: null, strike_taken: true}), {open: false});
n = $("lnote").innerHTML;
assert.ok(n.includes("Every strike near the money has already been traded today") && n.includes("no ticket"),
          "no free strike: the page says no ticket is coming and why - " + n);

const tk = {open: true, tracked_on: "premium", entry: 130.0, now: 131.0, strike: 25050, option_type: "CE",
            exit_at: "T2", lot_size: 65, lots: 1, targets: [145.0, 154.0, 160.0], stop: 110.0,
            hit: {T1: false, T2: false, T3: false}, hit_time: {}, sl_hit: false, odds: {},
            strike_swap: {from: 25000, to: 25050}};
ladder({}, tk);
n = $("lnote").innerHTML;
assert.ok(n.includes("25050") && n.includes("25000 had already been traded today"),
          "an open ticket that was swapped says so - " + n);
ladder({}, Object.assign({}, tk, {strike_swap: null}));
assert.ok(!$("lnote").innerHTML.includes("already been traded"), "an unswapped ticket says nothing of it");
console.log("ok:page");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    check("the Signal page says which strike was skipped and why, on the suggestion and on the ticket",
          "ok:page" in r.stdout and r.returncode == 0, out[-1200:])

print("SAME STRIKE TEST PASSED" if not fails else f"SAME STRIKE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
