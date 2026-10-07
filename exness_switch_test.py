#!/usr/bin/env python3
"""Bitcoin and gold on Exness (config.CRYPTO_VENUE = "exness", 3 Oct 2026): CFD tickets,
the closed-market hold, the feed's wiring, the journal rows, the AI desk's CFD proposals
and its prompts - hand-traced numbers, no network, no MetaApi token. The Indian indices
must come through all of it untouched."""
import datetime as dt
import os
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["ENABLE_CRYPTO"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tickets
import trade_log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_now = {"t": dt.datetime(2026, 10, 3, 21, 0, 0, tzinfo=IST)}
tickets.now_ist = lambda: _now["t"]

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE SETTINGS - BITCOIN AND GOLD FROM EXNESS, THE INDIAN INDICES UNTOUCHED")
check("the venue is exness and the crypto market reads from it",
      config.CRYPTO_VENUE == "exness" and config.MARKETS["crypto"]["market_provider"] == "exness")
btc, gold = config.INSTRUMENTS["BTC"], config.INSTRUMENTS["GOLD"]
check("BTC: Exness's BTCUSD, a CFD, 1 lot = 1 BTC", btc["provider"] == "exness" and btc["exness_symbol"] == "BTCUSD"
      and btc["cfd"] is True and btc["lot_size"] == 1.0)
check("GOLD: back on, Exness's XAUUSD, a CFD, 1 lot = 100 oz, its own ADX gate 25",
      gold.get("enabled") is True and gold["exness_symbol"] == "XAUUSD" and gold["cfd"] is True
      and gold["lot_size"] == 100.0 and config.vote_overrides("GOLD").get("adx") == 25)
check("both trade in the crypto market", config.instruments_in("crypto") == ["BTC", "GOLD"], config.instruments_in("crypto"))
check("the streamed symbol is the instrument key", config.crypto_index("BTC") == "BTC" and config.crypto_index("GOLD") == "GOLD")
check("lots are Exness lots (0.01 .. 1)", config.lot_choices("crypto") == [0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0],
      config.lot_choices("crypto"))
check("no taker flow, no option-spread cap, no Delta contracts-per-lot",
      not btc["taker_flow"] and not gold["taker_flow"] and config.max_spread_pct("GOLD") == float(config.MAX_SPREAD_PCT)
      and btc["contracts_per_lot"] is None)
check("the paper option seller is off (Exness has no options)", config.BTC_SELLER_PAPER is False)
check("the Delta settings are kept for a rollback", btc["delta_perpetual"] == "BTCUSD" and gold["delta_perpetual"] == "XAUTUSD")
nifty = config.INSTRUMENTS["NIFTY"]
check("NIFTY unchanged: kite, 65 lot, no cfd, its own market",
      config.MARKETS["nse_index"]["market_provider"] == "kite" and nifty["lot_size"] == 65
      and not config.is_cfd("NIFTY") and config.instruments_in("nse_index") == ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"])   # Midcap paper-only since 7 Oct 2026
check("the Indian indices keep the 2h breakeven; crypto still has none",
      config.time_breakeven_minutes("NIFTY") == 120 and config.time_breakeven_minutes("BTC") == 0
      and config.time_breakeven_minutes("GOLD") == 0)

print("2. CFD P&L - THE MOVE, LESS THE SPREAD, x LOT SIZE x LOTS")
t = {"option_type": "CE", "entry_spot": 84000.0, "lot_size": 1.0, "lots": 0.25, "entry_spread": 10.0}
check("buy 0.25 BTC at 84,000, out at 84,600: (600 - 10) x 0.25 = 147.50", tickets.cfd_pnl(t, 84600.0) == 147.5,
      tickets.cfd_pnl(t, 84600.0))
t2 = dict(t, option_type="PE")
check("SELL the same: 84,600 is a loss, (-600 - 10) x 0.25 = -152.50", tickets.cfd_pnl(t2, 84600.0) == -152.5)
check("SELL, out at 83,400: (600 - 10) x 0.25 = 147.50", tickets.cfd_pnl(t2, 83400.0) == 147.5)
g = {"option_type": "CE", "entry_spot": 4140.0, "lot_size": 100.0, "lots": 0.1, "entry_spread": 0.26}
check("gold 0.1 lot (10 oz) 4,140 -> 4,150: (10 - 0.26) x 10 = 97.40", tickets.cfd_pnl(g, 4150.0) == 97.4,
      tickets.cfd_pnl(g, 4150.0))
check("no spread on record -> the bare move", tickets.cfd_pnl(dict(t, entry_spread=None), 84100.0) == 25.0)
check("a missing price -> None, never a guess", tickets.cfd_pnl(t, None) is None)


def crypto_book():
    d = tempfile.mkdtemp()
    return tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d, "t.csv"))


def rec(index="BTC", side="CE", spot=84000.0, targets=(84300.0, 84500.0, 84800.0), stop=83700.0,
        spread=10.0, age=2.0, premium_source="estimate"):
    return {"index": index, "option_type": side, "bias": "BULLISH" if side == "CE" else "BEARISH",
            "suggested_strike": 84000, "option_chain": None, "spot": spot, "live_ltp": 950.0,
            "premium_source": premium_source, "index_targets": list(targets), "index_stop_loss": stop,
            "premium_targets": [1000.0, 1100.0, 1200.0], "premium_stop_loss": 800.0, "strike_swap": None,
            "cfd_spread": spread, "quote_age_s": age, "confidence": "High", "score": 4, "max_score": 5,
            "technical": {}, "adx": 30.0}


print("3. A CFD TICKET OPENS ON THE PRICE ITSELF - NEVER A PREMIUM, EVEN IF ONE WAS 'LIVE'")
bk = crypto_book()
b = bk.books["BTC"]
bk.lots = 0.25
bk._open(b, rec(premium_source="live"))
tr = b.trade
check("cfd frozen on, the spread frozen with it (10)", tr["cfd"] is True and tr["entry_spread"] == 10.0)
check("tracked on the price, not a premium (use_premium False although premium_source was live)",
      tr["use_premium"] is False and tr["entry_spot"] == 84000.0)
check("lot_size 1 (1 BTC) and the 0.25 lots frozen", tr["lot_size"] == 1.0 and tr["lots"] == 0.25)
pub = bk._public_trade(tr, live=84200.0)
check("the page's view: cfd, tracked on 'index' (the page's own word), P&L (200-10)x0.25 = 47.50",
      pub["cfd"] is True and pub["tracked_on"] == "index" and pub["pnl"] == 47.5 and pub["entry_spread"] == 10.0, pub["pnl"])

print("4. A SELL (PE): TARGETS BELOW, STOP ABOVE - AND THE CLOSE BANKS DOLLARS, LOGGED AS 'cfd'")
bk2 = crypto_book()
bk2.lots = 0.1
b2 = bk2.books["GOLD"]
bk2._open(b2, rec(index="GOLD", side="PE", spot=4140.0, targets=(4130.0, 4125.0, 4120.0), stop=4150.0, spread=0.26))
ev = bk2.tick_price("GOLD", 4129.0)
check("4,129 reaches T1 (4,130) on a sell and the stop ratchets down to T1",
      b2.trade["hit"]["T1"] and b2.trade["index_sl"] == 4130.0, b2.trade["index_sl"])
ev = bk2.tick_price("GOLD", 4119.5)
closed = [e for e in ev if e.get("kind") == "closed"]
want = round((4140.0 - 4119.5 - 0.26) * 100 * 0.1, 2)          # 202.40
check(f"4,119.5 reaches T3 (4,120): closed, booked ({4140}-{4119.5}-0.26) x 100 x 0.1 = {want}",
      closed and closed[0]["trade"]["pnl"] == want and bk2.session_net == want, (closed and closed[0]["trade"]["pnl"], bk2.session_net))
rows = trade_log._read_rows(bk2.path)
cl = [r for r in rows if r.get("event") == "CLOSE"]
check("the log row says tracked_on 'cfd' with the same pnl", cl and cl[0].get("tracked_on") == "cfd"
      and float(cl[0]["pnl"]) == want, cl and (cl[0].get("tracked_on"), cl[0].get("pnl")))
gold_pnl = want                    # kept apart: section 7 reuses the name "want"

print("5. NO TICKET ON A CLOSED MARKET'S LAST PRICE - OR WITH NO LIVE PRICE AT ALL")
bk3 = crypto_book()
check("a fresh quote (2 s) does not hold", bk3._closed_hold(rec(age=2.0)) is None)
h = bk3._closed_hold(rec(index="GOLD", age=2 * 86400.0))
check("gold, last tick two days ago: MARKET CLOSED", h and h[1] == "MARKET CLOSED" and "2880 minutes" in h[2], h)
h = bk3._closed_hold(rec(age=601.0))
check("just over CFD_STALE_QUOTE_S (600 s): closed", h and h[1] == "MARKET CLOSED")
check("exactly 600 s: still open", bk3._closed_hold(rec(age=600.0)) is None)
h = bk3._closed_hold(rec(age=None))
check("no live price yet: NO LIVE PRICE", h and h[1] == "NO LIVE PRICE")
check("an Indian index is never held by this", tickets.TicketBook(market="nse_index")._closed_hold(
      {"index": "NIFTY", "quote_age_s": None}) is None)
bk3.books["GOLD"].confirm_dir = ("BEARISH", "PE")
out = bk3._consider(bk3.books["GOLD"], rec(index="GOLD", side="PE", age=90000.0))
check("_consider: the stale gold reading opens nothing and says MARKET CLOSED",
      out == [] and bk3.books["GOLD"].trade is None and bk3.books["GOLD"].wait_reason[1] == "MARKET CLOSED",
      bk3.books["GOLD"].wait_reason)

print("6. NO 'STRIKE TAKEN' ON A CFD - IT HAS NO STRIKES")
bk4 = crypto_book()
bk4._contracts_cache = (9e18, {("BTC", 84000.0, "CE")})
check("BTC 84000 CE 'already traded' does not hold a CFD reading", bk4._strike_taken(bk4.books["BTC"], rec()) is None)
nk = tickets.TicketBook(market="nse_index")
nk._contracts_cache = (9e18, {("NIFTY", 25000.0, "CE")})
check("...while NIFTY 25000 CE still does", nk._strike_taken(nk.books["NIFTY"], {"index": "NIFTY",
      "suggested_strike": 25000, "option_type": "CE"}) is not None)

print("7. A SIZE SAVED IN DELTA CONTRACTS BECOMES EXNESS LOTS")
for saved, want in ((250, 0.25), (25, 0.02), (500, 0.5), (0.1, 0.1), (1, 1.0)):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.csv")
    with open(p + ".settings.json", "w") as fh:
        fh.write('{"capital": null, "risk_pct": 1.0, "lots": %s}' % saved)
    got = tickets.TicketBook(market="crypto", path=p).lots
    check(f"saved {saved} -> {want}", got == want, got)
d = tempfile.mkdtemp()
p = os.path.join(d, "t.csv")
with open(p + ".settings.json", "w") as fh:
    fh.write('{"capital": null, "risk_pct": 1.0, "lots": 3}')
check("NIFTY's saved 3 lots stay 3", tickets.TicketBook(market="nse_index", path=p).lots == 3.0)
cb = tickets.TicketBook(market="crypto")
cb.configure(lots=250)
check("a page still showing Delta's sizes asks for 250 -> 0.25 BTC, not a whole coin", cb.lots == 0.25, cb.lots)
cb.configure(lots=0.5)
check("an Exness size is taken as it is (0.5)", cb.lots == 0.5)
nb = tickets.TicketBook(market="nse_index")
nb.configure(lots=4)
check("NIFTY: 4 lots is 4 lots", nb.lots == 4.0)

print("8. THE FEED - EXNESS'S SHARED PROVIDER AND STREAMER, CFD READINGS STAMPED, OPTION FIELDS EMPTIED")
import exness_provider
import feeds
check("the provider for 'exness' is the ONE shared instance",
      feeds._crypto_provider("exness") is exness_provider.shared() is feeds._crypto_provider("exness"))
check("the streamer for 'exness' is an ExnessStreamer", isinstance(feeds._crypto_streamer("exness"), exness_provider.ExnessStreamer))
check("Delta still builds Delta's", type(feeds._crypto_provider("delta")).__name__ == "DeltaDataProvider")


class FakeStream:
    def quote(self, sym, max_age=None):
        return {"bid": 84000.0, "ask": 84012.0, "spread": 12.0} if sym == "BTC" else None
    def price_age(self, sym):
        return 3.5 if sym == "BTC" else None


fake_feed = type("F", (), {})()
fake_feed.dstream = FakeStream()
r = feeds.Feed._cfd_stamp(fake_feed, "BTC", rec(spread=None, age=None))
check("_cfd_stamp: the live spread (12) and the price age (3.5 s)", r["cfd_spread"] == 12.0 and r["quote_age_s"] == 3.5)
r2 = {"index": "NIFTY"}
check("...and leaves an Indian reading alone", feeds.Feed._cfd_stamp(fake_feed, "NIFTY", r2) == {"index": "NIFTY"})
pub = feeds._public(dict(rec(), technical={"total_score": 4}, trend={}), "BTC")
check("_public on BTC: cfd, no strike, no premium, no expiry, the Exness spread",
      pub["cfd"] is True and pub["strike"] is None and pub["ltp"] is None and pub["premium_targets"] == [None] * 3
      and pub["premium_stop"] is None and pub["expiry"] is None and pub["cfd_spread"] == 10.0
      and pub["targets"] == [84300.0, 84500.0, 84800.0] and pub["stop"] == 83700.0, {k: pub.get(k) for k in ("strike", "ltp")})
check("feeds._avoid_strikes is empty on a CFD", feeds.Feed._avoid_strikes(fake_feed, "BTC") == set())

print("9. THE JOURNAL - A CFD ROW IS A BUY OR A SELL, NO STRIKE, NO 'COST', NO CHARGES")
import journal
lines = journal._ticket_lines(bk2.path, "tool")                    # section 4's gold SELL
j = lines[0] if lines else {}
check("gold SELL: side SELL, dir sell, no strike", j.get("side") == "SELL" and j.get("dir") == "sell"
      and j.get("strike") is None, {k: j.get(k) for k in ("side", "dir", "strike")})
check(f"gross {gold_pnl}, charges 0 (the spread is already inside), net {gold_pnl}, not an estimate",
      j.get("gross") == gold_pnl and j.get("charges") == 0.0 and j.get("net") == gold_pnl
      and j.get("charges_estimated") is False, {k: j.get(k) for k in ("gross", "charges", "net", "charges_estimated")})
check("no 'cost' - a CFD's notional is not money paid", j.get("cost") is None)

print("10. THE AI DESK'S CFD PROPOSALS - PRICE LEVELS, CHECKED AGAINST THE LIVE PRICE")
import ai_desk


class DS:
    def index_price(self, sym):
        return 84000.0


desk = ai_desk.AIDesk.__new__(ai_desk.AIDesk)
desk.book = crypto_book()
desk.feed = type("F", (), {"dstream": DS(), "lock": threading.Lock(), "state": {}})()
ok, why, plan = desk.validate("BTC", rec(), {"option_type": "CE", "target": 84600, "stop": 83700})
check("BUY: stop 83,700 / target 84,600 at 84,000 -> ok, rr 2.0, entry the live price, no strike",
      ok and plan["rr"] == 2.0 and plan["ltp"] == 84000.0 and plan["strike"] is None and plan["cfd"], (ok, why, plan))
ok, why, _ = desk.validate("BTC", rec(), {"option_type": "CE", "target": 84600, "stop": 84100})
check("BUY with the stop ABOVE the price: rejected", not ok and "below" in why, why)
ok, why, _ = desk.validate("BTC", rec(), {"option_type": "PE", "target": 83400, "stop": 84300})
check("SELL: stop 84,300 / target 83,400 -> ok (rr 2.0)", ok, why)
ok, why, _ = desk.validate("BTC", rec(), {"option_type": "PE", "target": 84400, "stop": 84300})
check("SELL with the target ABOVE the price: rejected", not ok and "below" in why, why)
ok, why, _ = desk.validate("BTC", rec(), {"option_type": "CE", "target": 84200, "stop": 83700})
check("reward 200 / risk 300 = 0.67 under 1.0: rejected", not ok and "reward to risk" in why, why)
ok, why, _ = desk.validate("BTC", rec(), {"option_type": "CE", "target": 95000, "stop": 79000})
check("a stop 5.95% away (over 5%): rejected", not ok and "% away" in why, why)
ok, why, _ = desk.validate("BTC", rec(spread=10.0), {"option_type": "CE", "target": 84100, "stop": 83985})
check("a stop 15 away under twice the 10 spread: rejected", not ok and "spread" in why, why)
ok, why, _ = desk.validate("GOLD", rec(index="GOLD", age=90000.0), {"option_type": "CE", "target": 4200, "stop": 4100})
check("gold's market closed: rejected with the closed reason", not ok and "closed" in why, why)
ok, why, _ = desk.validate("BTC", rec(), {"option_type": "CE", "target": "x", "stop": 83700})
check("a target that is not a number: rejected", not ok and "number" in why, why)

print("11. THE AI PROMPTS DESCRIBE EXNESS, NOT DELTA'S OPTIONS")
import market_bot
for nm in ("SYSTEM", "DESK_SYSTEM"):
    txt = getattr(market_bot, nm)
    check(f"{nm}: the Exness passage is in, the Delta option passage is out",
          "THERE ARE NO OPTIONS ON BITCOIN OR GOLD HERE" in txt and "on Delta Exchange India, options on Bitcoin" not in txt)
    check(f"{nm}: still covers the Indian index options", "Nifty, Bank Nifty and Sensex" in txt)
check("DESK_SYSTEM: taker flow marked absent on Exness", "no trade tape" in market_bot.DESK_SYSTEM)
check("the entry tool says target/stop are PRICES on Exness",
      "the take-profit PRICE" in market_bot.DECISION_TOOLS["entry"]["input_schema"]["properties"]["target"]["description"])

print("12. A CFD EXIT PLAN - ONE TARGET AT 3R, NOTHING MOVES THE STOP; AND THE 24-HOUR LIMIT")
import signal_engine as se
OI = se.compute_option_chain_signal(None)


def tech(close, adx=30):
    return {"last_close": close, "last_rsi": 55.0, "last_atr": 300.0, "trend_score": 1, "macd_score": 1,
            "macd_hist": 5.0, "rsi_score": 1, "vwap_score": 1, "di_score": 0, "st_score": 0, "vol_score": 0,
            "adx": adx, "adx_ok": True, "vwap": close - 50, "vwap_gap": 50.0, "last_swing_low": close - 800,
            "last_swing_high": close + 800, "total_score": 4, "max_score": 4}


REACH = {"available": True, "reach_up": 1500.0, "reach_down": 1500.0, "cap_up": "the day's range",
         "cap_down": "the day's range"}
_shipped = dict(config.CFD_EXIT_PLAN)
config.CFD_EXIT_PLAN = {}
base = se.build_recommendation("BTC", tech(84000.0), OI, 400, reach=REACH)
config.CFD_EXIT_PLAN = _shipped
check("without a plan: the market-reach ladder (0.4/0.7/1.0 of 1,500)", base["target_basis"] == "market_reach"
      and base["index_targets"] == [84600.0, 85050.0, 85500.0] and base["gate_targets"] is None, base["index_targets"])
check("SHIPPED: one target at 1:1 for both BTC and GOLD (the user, 6 Oct 2026; 5R from 3 Oct before it)",
      config.CFD_EXIT_PLAN == {"BTC": {"plain_r": 1.0}, "GOLD": {"plain_r": 1.0}}, config.CFD_EXIT_PLAN)
was_plan = dict(config.CFD_EXIT_PLAN)
config.CFD_EXIT_PLAN = {}
base = se.build_recommendation("BTC", tech(84000.0), OI, 400, reach=REACH)
config.CFD_EXIT_PLAN = {"BTC": {"plain_r": 3.0}}
pr = se.build_recommendation("BTC", tech(84000.0), OI, 400, reach=REACH)
R = pr["risk_points"]
check(f"with plain_r 3: T1/T2/T3 = 1R/2R/3R from 84,000 (R = {R:g})",
      pr["target_basis"] == "plain_r" and pr["index_targets"] == [round(84000 + R, 2), round(84000 + 2 * R, 2),
                                                                  round(84000 + 3 * R, 2)], pr["index_targets"])
check("...the stop is the same one", pr["index_stop_loss"] == base["index_stop_loss"])
check("the ticket gate still reads the market-reach ladder it was tested on",
      pr["gate_targets"] == base["index_targets"] and tickets.reward_risk_t3("BTC", pr) == tickets.reward_risk_t3("BTC", base))
config.CFD_EXIT_PLAN = {"BTC": {"plain_r": 3.0}}
check("a plan is per instrument - set for BTC only, gold keeps the ladder", config.cfd_exit_plan("GOLD") is None)
check("an Indian index never takes a CFD plan", config.cfd_exit_plan("NIFTY") is None)
pub = feeds._public(dict(pr, technical={}, trend={}), "BTC")
check("the Signal card names T3 as the exit for a plain plan", pub["exit_at"] == "T3")
bk5 = crypto_book()
b5 = bk5.books["BTC"]
bk5.lots = 0.1
bk5._open(b5, dict(pr, cfd_spread=10.0, quote_age_s=1.0))
t5 = b5.trade
check("the ticket: plain_exit, exit at T3", t5["plain_exit"] is True and t5["exit_at"] == "T3")
stop0 = t5["index_sl"]
bk5.tick_price("BTC", 84000 + R + 1)
bk5.tick_price("BTC", 84000 + 2 * R + 1)
check("T1 and T2 reached: marked, but the stop has NOT moved", t5["hit"]["T1"] and t5["hit"]["T2"]
      and t5["index_sl"] == stop0 and t5["status"] == "OPEN", t5["index_sl"])
ev = bk5.tick_price("BTC", 84000 + 3 * R + 1)
check("T3 reached: closed as the full target", any(e.get("kind") == "closed" for e in ev) and "T3 hit" in t5["status"],
      t5["status"])
bk6 = crypto_book()
bk6._open(bk6.books["BTC"], dict(base, cfd_spread=10.0, quote_age_s=1.0))
t6 = bk6.books["BTC"].trade
check("without a plan the engine's own ladder is unchanged: not plain, exit at T2", t6["plain_exit"] is False
      and t6["exit_at"] == config.EXIT_AT_TARGET)
_now["t"] = _now["t"] + dt.timedelta(minutes=config.CFD_MAX_HOLD_MINUTES - 1)
bk6.tick_price("BTC", 84000.0)
check("23h59m in: still open", t6["status"] == "OPEN")
_now["t"] = _now["t"] + dt.timedelta(minutes=2)
ev = bk6.tick_price("BTC", 84010.0)
check("24 hours: closed at the market as a time limit", t6["status"].startswith("CLOSED — time limit (24 hours)")
      and any(e.get("kind") == "closed" for e in ev), t6["status"])
nb6 = tickets.TicketBook(market="nse_index")
nt = {"index": "NIFTY", "option_type": "CE", "strike": 25000, "entry_time": "10:00:00", "entry_spot": 25000.0,
      "entry_ts": _now["t"] - dt.timedelta(days=2), "entry_ltp": 100.0, "use_premium": True, "lot_size": 65, "lots": 1,
      "exit_at": "T2", "index_targets": [], "index_sl": None, "premium_targets": [120.0, 140.0, 160.0],
      "premium_sl": 80.0, "hit": {"T1": False, "T2": False, "T3": False}, "hit_time": {"T1": None, "T2": None, "T3": None},
      "sl_hit": False, "sl_hit_time": None, "time_breakeven_done": True, "status": "OPEN", "trade_id": "N-1"}
nb6.books["NIFTY"].trade = nt
nb6.tick_price("NIFTY", 100.0)
check("an Indian option ticket has no such limit (2 days old, still open)", nt["status"] == "OPEN", nt["status"])
config.CFD_EXIT_PLAN = was_plan
bk7 = crypto_book()
bk7._open(bk7.books["BTC"], dict(base, cfd_spread=10.0, quote_age_s=1.0))
newer = bk7.books["BTC"].trade["trade_id"]
check("a broker close naming an OLDER ticket leaves the newer one open",
      bk7.close_ticket("BTC", "CLOSED — stop-loss hit (filled at Exness)", 83000.0, trade_id="BTC-older") is None
      and bk7.books["BTC"].trade is not None)
check("...the same close naming THIS ticket closes it", bk7.close_ticket("BTC", "CLOSED — stop-loss hit (filled at Exness)",
      83000.0, trade_id=newer) is not None and bk7.books["BTC"].trade is None)

print()
if fails:
    print(f"EXNESS SWITCH TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("EXNESS SWITCH TEST PASSED")
