#!/usr/bin/env python3
"""btc_seller.py - the paper BTC option seller. Hand-traced with a fake Delta
provider and a fake clock: no network, no order code anywhere."""
import datetime as dt
import math
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import btc_seller as bs
import config

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


UTC = dt.timezone.utc
at = lambda y, m, d, h, mi=0: dt.datetime(y, m, d, h, mi, tzinfo=UTC)

print("1. THE PIECES")
check("Delta's fee: $600 premium at $84,000 -> notional 8.40 (under the 21.00 cap) x 1.18 = 9.912",
      abs(bs.option_fee(600, 84000) - 9.912) < 1e-9)
check("...and the 3.5% cap binds on a $100 premium -> 4.13", abs(bs.option_fee(100, 84000) - 4.13) < 1e-9)
# Closes alternating x1.01 / /1.01: every 15-minute log return is +-ln(1.01); std ~ ln(1.01).
closes = [80000.0]
for k in range(96 * 7 + 5):
    closes.append(closes[-1] * (1.01 if k % 2 == 0 else 1 / 1.01))
rv = bs.realised_vol_7d(closes)
want = math.log(1.01) * math.sqrt(96 * 7 / (96 * 7 - 1)) * math.sqrt(96 * 365)
check("realised vol = std of the last 7 days' 15-minute log returns x sqrt(96 x 365)", abs(rv - want) < 1e-9,
      (round(rv, 4), round(want, 4)))
check("fewer than 6 days of bars -> None, not a guess", bs.realised_vol_7d(closes[:96 * 5]) is None)


def row(exp, k, kind, bid, ask, iv):
    return {"expiry": exp, "strike": k, "kind": kind, "bid": bid, "ask": ask, "iv": iv, "spot": 84000.0}


now = at(2026, 10, 2, 12, 5)                      # 17:35 IST
rows = [row("2026-10-02", 84000, "C", 10, 12, 40), row("2026-10-02", 84000, "P", 10, 12, 40),   # settling now
        row("2026-10-03", 83800, "C", 800, 830, 45), row("2026-10-03", 83800, "P", 600, 625, 45),
        row("2026-10-03", 84000, "C", 690, 720, 46), row("2026-10-03", 84000, "P", None, 720, 46),  # put: no bid
        row("2026-10-03", 84200, "C", 600, 625, 47), row("2026-10-03", 84200, "P", 790, 820, 47),
        row("2026-10-04", 84000, "C", 950, 990, 44), row("2026-10-04", 84000, "P", 940, 980, 44)]
exp, c, p = bs.pick_contract(rows, 84050.0, now)
check("picks the daily settling ~24h out (3 Oct), not the one settling now or the 2-day one", exp == "2026-10-03", exp)
check("84,000 is nearest but its put has no bid - the nearest strike with BOTH legs quoted (83,800 vs 84,200:"
      " 84,050 is 250 from 83,800 and 150 from 84,200) -> 84,200", c["strike"] == 84200 and p["strike"] == 84200, c["strike"])

print("2. THE DECISION")
d = bs.decide(rows, 84050.0, closes, now)          # iv 47% vs rv ~ ln(1.01)*sqrt(35040) = 186% -> not expensive
check("implied 47% vs realised ~186%: NOT expensive - skipped, but still a full decision",
      d["sell"] is False and d["expiry"] == "2026-10-03" and "not expensive" in d["reason"], d["reason"])
calm = [80000.0 * (1 + 0.0002 * math.sin(k)) for k in range(96 * 7 + 5)]
d = bs.decide(rows, 84050.0, calm, now)
check("implied 47% vs a calm week's tiny realised vol: expensive -> SELL", d["sell"] is True, d["reason"])
check("the credit is the two BIDS (600 + 790), not the marks or the asks - the spread is paid",
      d["credit"] == 1390.0 and d["bid_call"] == 600 and d["bid_put"] == 790, d["credit"])
check("implied vol = the mean of the two legs' mark IV (47%)", abs(d["iv"] - 0.47) < 1e-12)

print("3. SETTLEMENT")
pos = {"strike": 84200, "credit": 1390.0, "fees_in": 5.0, "lots": 250}
r = bs.settle_pnl(pos, 84000.0)
check("settles 200 under the strike: the put is worth 200, the call nothing; the put's fee is charged",
      r["intrinsic_put"] == 200.0 and r["intrinsic_call"] == 0.0 and abs(r["fees_out"] - round(bs.option_fee(200, 84000), 2)) < 1e-9)
want = 1390 - 5 - 200 - bs.option_fee(200, 84000)
check("net per BTC = credit - entry fees - intrinsic - settlement fee; $ = x 250 x 0.001",
      abs(r["pnl_per_btc"] - round(want, 2)) < 1e-9 and abs(r["pnl_usd"] - round(want * 0.25, 2)) < 0.011,
      (r["pnl_per_btc"], r["pnl_usd"]))

print("4. THE BOOK - ONE DECISION A DAY, SOLD AT THE BIDS, SETTLED NEXT DAY")


class FakeProvider:
    def __init__(self):
        self.rows, self.px, self.calls = rows, 84050.0, []
    def _chain_rows(self, key):
        self.calls.append("chain")
        return self.rows
    def spot(self, key):
        self.calls.append("spot")
        return self.px
    def get_ohlc(self, key, interval, lookback_days=None):
        self.calls.append("ohlc")
        return pd.DataFrame({"Close": calm})


was = config.BTC_SELLER_PAPER
config.BTC_SELLER_PAPER = True
folder = tempfile.mkdtemp()
fp = FakeProvider()
book = bs.SellerBook(folder, fp, lots=250)
check("before 17:30 IST: nothing at all - not even a chain fetch", book.tick(at(2026, 10, 2, 11, 55)) == "idle"
      and fp.calls == [])
check("17:35 IST: decides and SELLS", book.tick(now) == "sold", book.state.get("last_decision", {}).get("reason"))
op = book.state["open"]
check("the open position is the 3 Oct 84,200 straddle at 1,390 credit, settling 3 Oct 12:00 UTC",
      op["expiry"] == "2026-10-03" and op["strike"] == 84200 and op["credit"] == 1390.0
      and op["settle_at"] == "2026-10-03T12:00:00+00:00", op)
check("17:45 the same day: no second decision", book.tick(at(2026, 10, 2, 12, 15)) == "idle")
with open(os.path.join(folder, "decisions.csv")) as fh:
    check("exactly one decision row was logged for the day", len(fh.read().strip().splitlines()) == 2)
book2 = bs.SellerBook(folder, fp, lots=250)
check("a restart reads the open position back from disk", book2.state["open"]["strike"] == 84200)
fp.px = 84000.0
fp.rows = [row("2026-10-04", 84000, "C", 700, 730, 44), row("2026-10-04", 84000, "P", 690, 720, 44)]
did = book2.tick(at(2026, 10, 3, 12, 1))           # 17:31 IST the next day
check("next day 17:31 IST: the old straddle SETTLES first, then the next day is decided", did == "settled,sold", did)
t = book2.trades()
check("the settled trade is logged with its result", len(t) == 1 and abs(float(t[0]["pnl_per_btc"])
      - round(1390 - op["fees_in"] - 200 - bs.option_fee(200, 84000), 2)) < 0.011, t[0]["pnl_per_btc"] if t else None)

print("5. NOT A DECISION YET -> WAIT AND RETRY, DON'T WRITE THE DAY OFF")
folder2 = tempfile.mkdtemp()
fp2 = FakeProvider()
fp2.rows = [row("2026-10-02", 84000, "C", 10, 12, 40), row("2026-10-02", 84000, "P", 10, 12, 40)]   # no next-day yet
b3 = bs.SellerBook(folder2, fp2, lots=250)
check("no next-day contract quoted yet: 'waiting', nothing logged", b3.tick(now) == "waiting"
      and not os.path.exists(os.path.join(folder2, "decisions.csv")))
fp2.rows = rows
check("a minute later it is listed: decided then", b3.tick(at(2026, 10, 2, 12, 6)) == "sold")
check("after the hour's window closes, no late decision", bs.SellerBook(tempfile.mkdtemp(), FakeProvider()).tick(
      at(2026, 10, 2, 13, 5)) == "idle")

print("6. SWITCHED OFF, AND PAPER ONLY")
config.BTC_SELLER_PAPER = False
check("BTC_SELLER_PAPER False: the tick does nothing", bs.SellerBook(tempfile.mkdtemp(), FakeProvider()).tick(now) == "off")
config.BTC_SELLER_PAPER = was
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "btc_seller.py")).read()
check("the module imports no order code and touches no keys",
      all(w not in src for w in ("import delta_orders", "import live_orders", "place_order(", "import user_delta",
                                   "api_key", "api_secret")))

print("7. WHAT THE PAGE SEES")
b4 = bs.SellerBook(tempfile.mkdtemp(), FakeProvider(), lots=250)
config.BTC_SELLER_PAPER = True
b4.tick(now)
b4.mark = 1300.0
pub = b4.public()
check("open position with its buy-back value and unrealised $ ((1,390 - fees - 1,300) x 0.25)",
      pub["open"]["buyback_now"] == 1300.0 and abs(pub["open"]["unrealised_usd"]
      - round((1390 - pub["open"]["fees_in"] - 1300) * 0.25, 2)) < 0.011, pub["open"])
check("marked paper, with the record and the last decision", pub["paper"] is True and pub["record"]["trades"] == 0
      and pub["last_decision"]["sell"] is True)
config.BTC_SELLER_PAPER = was

print()
if fails:
    print(f"BTC SELLER TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("BTC SELLER TEST PASSED")
