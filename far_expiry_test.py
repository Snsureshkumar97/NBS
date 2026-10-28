#!/usr/bin/env python3
"""The far expiry (config.EXPIRY_CHOICES "far"; the user, 28 Oct 2026: "build it, off by default, paper only"):
an index set to it in Trade settings buys the at-the-money contract of the first expiry at least FAR_EXPIRY_DAYS
days out instead of the nearest - the signal unchanged, the levels moved onto that contract's own price - holds when
that contract has no price, and never places a real order (live_orders_test.py checks that). Runs the real
TicketBook against a pinned clock; no network."""
import datetime as dt
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
import signal_engine
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


def rec(side="CE", spot=24500.0, live=120.0):
    ce = side == "CE"
    step = (60., 110., 160.)
    return {"index": "NIFTY", "bias": "BULLISH" if ce else "BEARISH", "option_type": side,
            "suggested_strike": 24500 if ce else 24200, "spot": spot,
            "index_targets": [spot + s for s in step] if ce else [spot - s for s in step],
            "index_stop_loss": spot - 60. if ce else spot + 60.,
            "premium_targets": [live + 30, live + 55, live + 80] if live else [130., 150., 170.],
            "premium_stop_loss": live - 30 if live else 80.,
            "premium_source": "live" if live else None, "live_ltp": live,
            "option_chain": {"expiry": "2026-11-03"} if live else None, "target_basis": "market_reach",
            "risk_points": 60.0, "reach_points": 160.0, "reach_to_risk": 2.67,
            "opening_range": {"ready": True, "high": 24400.0, "low": 24300.0},
            "spread": None, "technical": {"adx": 30}}


FAR = {"expiry": "2026-11-17", "ltp": 300.0, "bid": 299.0, "ask": 301.0, "tradingsymbol": "NIFTY26N1724500CE"}


def book(path=None):
    return tickets.TicketBook(owner=None, market="nse_index", path=path or os.path.join(tempfile.mkdtemp(), "t.csv"))


print("1. OFF BY DEFAULT")
check("config: nearest by default, 14 days, never real orders",
      config.EXPIRY_CHOICES == ("nearest", "far") and config.EXPIRY_DEFAULT == "nearest"
      and config.FAR_EXPIRY_DAYS == 14 and config.FAR_EXPIRY_LIVE_ORDERS is False)
b = book()
check("every index starts on the nearest", [b.expiry_for(k) for k in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")]
      == ["nearest"] * 4)
ses = b.session()
check("the page gets the setting and its choices", ses["expiry_by"]["NIFTY"] == "nearest"
      and ses["expiry_choices"] == ["nearest", "far"] and ses["far_expiry_days"] == 14)

print("2. THE SETTING, PER INDEX")
path = os.path.join(tempfile.mkdtemp(), "t.csv")
b = book(path)
b.configure(expiry="far", expiry_index="SENSEX")
check("Sensex set to far, Nifty untouched", b.expiry_for("SENSEX") == "far" and b.expiry_for("NIFTY") == "nearest")
check("saved across a restart", book(path).expiry_for("SENSEX") == "far")
b.configure(expiry="monthly", expiry_index="NIFTY")
b.configure(expiry="far", expiry_index="GOLD")
check("an unknown choice or index changes nothing", b.expiry_for("NIFTY") == "nearest" and "GOLD" not in b.expiry_by)
cb = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(tempfile.mkdtemp(), "t.csv"))
check("off the Indian market there is no such setting", cb.expiry_for("BTC") is None)

print("3. THE LEVELS ON THE FAR CONTRACT")
r = rec()
m = signal_engine.onto_contract(r, FAR)
check("entry at the far contract's price", m["live_ltp"] == 300.0 and m["premium_source"] == "live")
check("targets: its price plus the index's move to each level x 0.5", m["premium_targets"] == [330.0, 355.0, 380.0],
      m["premium_targets"])
check("stop: its price less the move to the stop x 0.5", m["premium_stop_loss"] == 270.0, m["premium_stop_loss"])
check("the nearest's price kept beside it", m["chain_ltp"] == 120.0 and m["far_contract"]["expiry"] == "2026-11-17")
check("no price, or no index levels: None", signal_engine.onto_contract(r, {"ltp": None}) is None
      and signal_engine.onto_contract(dict(r, index_targets=None), FAR) is None)
check("the reading itself is not changed", r["live_ltp"] == 120.0)

print("4. THE FAR CONTRACT, OR A HOLD")
b = book()
r = rec()
check("on the nearest expiry nothing is asked", b._far_hold(r) is None and "far_contract" not in r)
b.configure(expiry="far", expiry_index="NIFTY")
check("far, and nothing to ask: held", (b._far_hold(rec()) or ("",))[0] == "no_far_price")
b.far_quote = lambda name, strike, side: None
check("far, and no price for the contract: held", (b._far_hold(rec()) or ("",))[0] == "no_far_price")
def boom(*a):
    raise RuntimeError("Kite down")
b.far_quote = boom
check("far, and the lookup fails: held", (b._far_hold(rec()) or ("",))[0] == "no_far_price")
asked = []
b.far_quote = lambda name, strike, side: (asked.append((name, strike, side)), FAR)[1]
r = rec("PE")
check("far, with a price: no hold, and the contract is on the reading", b._far_hold(r) is None
      and r["far_contract"] == FAR)
check("...asked for THIS ticket's own strike and side", asked == [("NIFTY", 24200, "PE")], asked)

print("5. A TICKET OPENED ON THE FAR CONTRACT")
b._open(b.books["NIFTY"], dict(rec(), far_contract=FAR))
t = b.books["NIFTY"].trade
check("its expiry is the far one", t["expiry"] == "2026-11-17", t["expiry"])
check("marked far, with its contract", t["far_expiry"] is True and t["contract"] == "NIFTY26N1724500CE")
check("frozen from the far price: entry, targets, stop", t["entry_ltp"] == 300.0 and t["use_premium"] is True
      and t["premium_targets"] == [330.0, 355.0, 380.0] and t["premium_sl"] == 270.0,
      (t["entry_ltp"], t["premium_targets"], t["premium_sl"]))
check("the page is told", b._public_trade(t)["far_expiry"] is True and b._public_trade(t)["expiry"] == "2026-11-17")
nb = book()
nb._open(nb.books["NIFTY"], dict(rec(), far_contract=FAR))
t = nb.books["NIFTY"].trade
check("on the nearest expiry a stray far contract is ignored: the chain's own expiry and price",
      t["expiry"] == "2026-11-03" and t["far_expiry"] is False and t["entry_ltp"] == 120.0, (t["expiry"], t["entry_ltp"]))

print("6. THROUGH THE REAL GATES")
def feed(b, r, n):
    evs = []
    for _ in range(n):
        CLOCK["s"] += 1
        evs += b.update("NIFTY", dict(r))
    return evs
CLOCK["s"] += 3600
b = book()
b.configure(expiry="far", expiry_index="NIFTY")
evs = feed(b, rec(), config.SIGNAL_CONFIRM_SECONDS + config.SIGNAL_CONFIRM_TICKS + 5)
check("far with no price: no ticket, and the reason says so", not any(e.get("kind") == "opened" for e in evs)
      and (b.books["NIFTY"].wait_reason or ("",))[0] == "no_far_price", b.books["NIFTY"].wait_reason)
b.far_quote = lambda name, strike, side: FAR
evs = feed(b, rec(), 3)
opened = [e for e in evs if e.get("kind") == "opened"]
check("the price arrives: the ticket opens on the far contract", opened and opened[0]["trade"]["far_expiry"] is True
      and opened[0]["trade"]["expiry"] == "2026-11-17", [e.get("kind") for e in evs])

print("7. THE FEED")
import feeds
src = open(os.path.join(HERE, "feeds.py")).read()
check("the tickets get the lookup", "self.tickets.far_quote = self._far_quote" in src)
check("an open ticket's stream is its OWN expiry's contract",
      'tok = provider.option_token(trade["index"], trade["strike"],\n'
      '                                        trade["option_type"], trade.get("expiry"))' in src)
calls = []
class Prov:
    def far_contract(self, name, strike, side, days):
        calls.append((name, strike, side, days))
        return FAR
fake = types.SimpleNamespace(far_cache={}, _provider_for=lambda name, p: Prov(), _note_fault=lambda *a: None)
t0 = feeds.time.time
clock = {"t": 1000.0}
feeds.time = types.SimpleNamespace(time=lambda: clock["t"])
got = feeds.Feed._far_quote(fake, "NIFTY", 24500, "CE")
feeds.Feed._far_quote(fake, "NIFTY", 24500, "CE")
check("asks the provider for 14 days out, once while it is fresh", got == FAR and calls == [("NIFTY", 24500.0, "CE", 14)],
      calls)
clock["t"] += feeds.FAR_QUOTE_S + 1
feeds.Feed._far_quote(fake, "NIFTY", 24500, "CE")
check("...and again once that has gone stale", len(calls) == 2)
fake2 = types.SimpleNamespace(far_cache={}, _provider_for=lambda name, p: object(), _note_fault=lambda *a: None)
check("a provider that cannot say (not Kite): None", feeds.Feed._far_quote(fake2, "NIFTY", 24500, "CE") is None)

print("8. THE PROVIDER")
import data_providers as dp
p = dp.KiteDataProvider.__new__(dp.KiteDataProvider)
p.get_expiry_dates = lambda k: ["2026-10-29", "2026-11-03", "2026-11-10", "2026-11-17", "2026-11-24"]
picked = []
p._option_pick = lambda k, strike, opt, exp: (picked.append(exp), {"expiry": dt.date.fromisoformat(exp),
                                                                     "_exchange": "NFO",
                                                                     "tradingsymbol": "NIFTY26N1224500CE"})[1]
p.kite = types.SimpleNamespace(quote=lambda syms: {syms[0]: {"last_price": 288.5, "depth": {
    "buy": [{"price": 288.0, "quantity": 75}], "sell": [{"price": 289.0, "quantity": 75}]}}})
got = p.far_contract("NIFTY", 24500, "CE", 14, today=dt.date(2026, 10, 29))
check("the first expiry at least 14 days out (17 Nov from 29 Oct), with its live quote",
      got and got["expiry"] == "2026-11-17" and got["ltp"] == 288.5 and picked == ["2026-11-17"], (got, picked))
p.kite = types.SimpleNamespace(quote=lambda syms: {})
check("no quote: None", p.far_contract("NIFTY", 24500, "CE", 14, today=dt.date(2026, 10, 29)) is None)
check("no expiry that far out: None", p.far_contract("NIFTY", 24500, "CE", 60, today=dt.date(2026, 10, 29)) is None)

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
