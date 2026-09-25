#!/usr/bin/env python3
"""A Bitcoin ticket is frozen from the price the order will be priced from (25 Sep 2026).

The user asked why the tool's entry (1,211.43) and Delta's fill (1,294.10) were so far apart. The ticket froze the option
chain's price - a REST snapshot up to 30 s old - while the order was priced a second later from the live mark (1,268.7, plus
a 2% cushion). The stop and the targets, frozen from the stale price, were really 34% / 24% from the entry, not the 29% /
32% shown: T2 : stop 0.72, not 1.12. Now the feed hands the ticket book the live mark of the suggested contract and the
book re-anchors entry, stop and targets on it at the moment of issue. Fakes only.
"""
import copy, datetime as dt, math, os, sys, tempfile, types

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, feeds, signal_engine as se, tickets, trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

OLD, FRESH = 1211.43, 1268.7
REC = {"live_ltp": OLD, "premium_source": "live", "target_basis": "market_reach",
       "premium_targets": [1436.49, 1605.29, 1774.08], "premium_stop_loss": 859.76}

print("1. THE REBASE")
r = se.rebase_premium(REC, FRESH)
d = FRESH - OLD
check("the entry moves to the fresh price, the snapshot's is kept beside it", r["live_ltp"] == 1268.7 and r["chain_ltp"] == OLD, (r["live_ltp"], r.get("chain_ltp")))
check("built by addition (market_reach): every level moves by the same amount, so their distances from the entry are unchanged",
      all(abs((n - r["live_ltp"]) - (o - OLD)) < 0.02 for n, o in zip(r["premium_targets"], REC["premium_targets"]))
      and abs((r["live_ltp"] - r["premium_stop_loss"]) - (OLD - REC["premium_stop_loss"])) < 0.02, (r["premium_targets"], r["premium_stop_loss"]))
check("...so the stop is still 351.67 below the entry, not the 434.34 the fill really sat above it", abs((r["live_ltp"] - r["premium_stop_loss"]) - 351.67) < 0.02)
check("the rebase does not touch what it was given", REC["live_ltp"] == OLD and REC["premium_targets"][0] == 1436.49)
pct = dict(REC, target_basis="risk_multiple", premium_targets=[OLD * 1.2, OLD * 1.4, OLD * 1.75], premium_stop_loss=OLD * 0.7)
r = se.rebase_premium(pct, FRESH)
ratio = FRESH / OLD
check("built by percentage: every level moves by the ratio, so the percentages are the same", abs(r["premium_stop_loss"] - OLD * 0.7 * ratio) < 0.02
      and abs(r["premium_targets"][2] - OLD * 1.75 * ratio) < 0.02 and abs(r["premium_stop_loss"] / r["live_ltp"] - 0.7) < 0.001)
check("a stop that would go below 5 cents by addition stops at 5 cents", se.rebase_premium(dict(REC, live_ltp=10, premium_targets=[12, 13, 14], premium_stop_loss=1.0), 8.0)["premium_stop_loss"] == 0.05)
for name, args in (("no fresh price", (REC, None)), ("a price of zero", (REC, 0)), ("a negative price", (REC, -3.0)), ("not a number", (REC, "abc")),
                   ("NaN", (REC, float("nan"))), ("the same price", (REC, OLD)), ("a move over 30% (a bad tick)", (REC, OLD * 1.31)),
                   ("a drop over 30% (a bad tick)", (REC, OLD * 0.69)),
                   ("premium not live", (dict(REC, premium_source="approx_move"), FRESH)), ("no live price in the reading", (dict(REC, live_ltp=None), FRESH)),
                   ("no levels", (dict(REC, premium_targets=None), FRESH)), ("a missing target", (dict(REC, premium_targets=[1, None, 3]), FRESH)),
                   ("no stop", (dict(REC, premium_stop_loss=None), FRESH))):
    check(f"{name}: handed back as it was", se.rebase_premium(*args) is args[0])
check("a 29% move is still a move", se.rebase_premium(REC, OLD * 1.29)["live_ltp"] == round(OLD * 1.29, 2))

print("2. THE TICKET IS FROZEN FROM IT")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = dt.datetime(2026, 9, 8, 11, 0, 0, tzinfo=IST); CLOCK = {"s": 0.0}
tickets.now_ist = lambda: BASE + dt.timedelta(seconds=CLOCK["s"])
tickets.time = types.SimpleNamespace(time=lambda: 1_780_000_000 + CLOCK["s"])
tickets.is_market_open = lambda *a, **k: True
tickets._safe_explain = lambda rec: {"verdict": "test"}
def book_with(fresh_cb):
    LOG = os.path.join(tempfile.mkdtemp(), "trades.csv"); trade_log._log_path = lambda: LOG
    b = tickets.TicketBook(owner=None, market="nse_index"); b.fresh_price = fresh_cb
    return b, LOG
def sig(**kw):
    rec = {"index": "NIFTY", "bias": "BEARISH", "option_type": "PE", "suggested_strike": 24200, "spot": 24200.0,
           "index_targets": [24140., 24090., 24040.], "index_stop_loss": 24260.,
           "premium_targets": [1436.49, 1605.29, 1774.08], "premium_stop_loss": 859.76, "premium_source": "live",
           "live_ltp": OLD, "target_basis": "market_reach", "option_chain": None, "risk_points": 60.0, "reach_points": 160.0,
           "reach_to_risk": 2.67, "opening_range": {"ready": True, "high": 24400.0, "low": 24300.0}, "spread": None, "technical": {"adx": 30}}
    rec.update(kw); return rec
def issue(b, rec=None, n=130):
    for _ in range(n):
        CLOCK["s"] += 1
        b.update("NIFTY", dict(rec or sig()))
    return b.books["NIFTY"].trade
seen = []
b, LOG = book_with(lambda name, rec: (seen.append((name, rec["suggested_strike"], rec["option_type"])), FRESH)[1])
t = issue(b)
check("the ticket is frozen from the fresh price: entry, and the stop and targets with it", t and t["entry_ltp"] == 1268.7
      and abs((t["entry_ltp"] - t["premium_sl"]) - 351.67) < 0.02 and t["premium_targets"][1] == round(1605.29 + d, 2), (t and t["entry_ltp"], t and t["premium_sl"]))
check("the callback was asked for the index and the contract the reading names", seen and seen[0] == ("NIFTY", 24200, "PE"), seen[:1])
import csv
rows = list(csv.DictReader(open(LOG)))
check("the log's OPEN row carries the price the ticket was frozen from", rows and float(rows[0]["entry"]) == 1268.7, rows[0]["entry"] if rows else None)
pub = b.public("NIFTY")["ticket"]
check("the page's ticket shows the same entry, stop and first target", pub["entry"] == 1268.7 and pub["stop"] == t["premium_sl"] and pub["targets"][0] == t["premium_targets"][0])
for name, cb in (("a callback that has nothing", lambda n, r: None), ("a callback that raises", lambda n, r: [][3]), ("a bad tick (+40%)", lambda n, r: OLD * 1.4), ("no callback", None)):
    b, LOG = book_with(cb); t = issue(b)
    check(f"{name}: frozen from the chain's price, exactly as before", t and t["entry_ltp"] == OLD and t["premium_sl"] == 859.76 and t["premium_targets"][0] == 1436.49, t and t["entry_ltp"])
b, LOG = book_with(lambda n, r: FRESH); t = issue(b, sig(premium_source="approx_move", live_ltp=None, premium_targets=[10., 20., 30.], premium_stop_loss=5.0))
check("a ticket priced off the index (no live premium) is not rebased", t and t["entry_ltp"] is None and t["use_premium"] is False)

print("3. THE FEED SUPPLIES THE LIVE MARK - CRYPTO, THE CONTRACT NAMED, ARRIVED JUST NOW")
config.ENABLE_CRYPTO = True
f = feeds.Feed("t:fresh", "fresh@example.invalid", "crypto")
check("the rule book is given it, the AI desk's book is not (its levels are the model's own)", f.tickets.fresh_price == f._fresh_premium and f.ai.book.fresh_price is None)
class Stream:
    def __init__(self, mark, age): self.mark, self.age, self.asked = mark, age, []
    def book(self, inst, max_age=None):
        self.asked.append((inst, max_age))
        return None if (max_age is not None and self.age > max_age) else {"mark": self.mark, "at": 0}
f.sug_tokens["BTC"] = (84000, "PE", "P-BTC-84000-021026")
f.dstream = Stream(1268.7, 1.0)
rec = {"suggested_strike": 84000, "option_type": "PE"}
check("the live mark of the suggested contract", f._fresh_premium("BTC", rec) == 1268.7 and f.dstream.asked[-1] == ("P-BTC-84000-021026", feeds.FRESH_MARK_MAX_AGE_S), f.dstream.asked)
check("a different strike is not used", f._fresh_premium("BTC", {"suggested_strike": 84500, "option_type": "PE"}) is None)
check("the other side is not used", f._fresh_premium("BTC", {"suggested_strike": 84000, "option_type": "CE"}) is None)
f.dstream = Stream(1268.7, 30.0)
check("a mark older than a few seconds (a stalled socket) is not used", f._fresh_premium("BTC", rec) is None)
f.dstream = None
check("no socket: nothing", f._fresh_premium("BTC", rec) is None)
f.dstream = Stream(1268.7, 1.0); f.sug_tokens.pop("BTC")
check("no contract streaming for that index: nothing", f._fresh_premium("BTC", rec) is None)
fn = feeds.Feed("t:fresh:nse", "fresh2@example.invalid", "nse_index")
fn.sug_tokens["NIFTY"] = (24200, "PE", 12345); fn.dstream = Stream(1268.7, 1.0)
check("the Indian market is left as it was (its chain is streamed already)", fn._fresh_premium("NIFTY", {"suggested_strike": 24200, "option_type": "PE"}) is None)

print("FRESH ENTRY TEST PASSED" if not fails else f"FRESH ENTRY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
