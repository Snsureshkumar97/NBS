#!/usr/bin/env python3
"""cfd_rules.py - the live votes must be the ones cfd_vote_search.py scored, bar for bar, and
a rule's reading must become exactly the ticket that was tested. Needs the Exness history cache
(exness_data.py) for part 1; skipped there without it."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
os.environ["ENABLE_CRYPTO"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import cfd_rules
import config

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE LIVE VOTES ARE THE SEARCH'S VOTES - from 30 days of candles, as live, at random bars")
try:
    import exness_data as ed
    import cfd_vote_search as vs
    have = all(os.path.exists(os.path.join(ed._dir(), f)) for f in ("BTCUSD_2026_08_15m.csv", "XAUUSD_2026_08_15m.csv"))
except Exception:
    have = False
if not have:
    print("  (skipped: no Exness history cache on this machine)")
else:
    for sym, key in (("BTCUSD", "BTC"), ("XAUUSD", "GOLD")):
        full = ed.load(sym)
        V, F = vs.votes_and_filters(full, key, [None] * len(full))
        ref = dict(V, **F)
        win = 96 * cfd_rules.HISTORY_DAYS
        rng = np.random.default_rng(7)
        bars = rng.choice(np.arange(win, len(full) - 1), 300, replace=False)
        names = sorted({n for p in config.CFD_RULES.values() for n in p["votes"] + p["filters"]})
        bad = {n: 0 for n in names}
        sides_bad = 0
        plan = config.CFD_RULES[key]
        for i in bars:
            df = full.iloc[i - win + 1:i + 1]
            a = cfd_rules.compute(df)
            for n in names:
                if (int(a[n][-1]) if a[n].dtype != bool else bool(a[n][-1])) != \
                   (int(ref[n][i]) if ref[n].dtype != bool else bool(ref[n][i])):
                    bad[n] += 1
            # as live: the next candle has started (it is in the data), a minute after the close
            ev = cfd_rules.evaluate(key, full.iloc[i - win + 1:i + 2], now=df.index[-1] + cfd_rules.BAR + pd.Timedelta(minutes=1))
            vs_ = [ref[v][i] for v in plan["votes"]]
            want = vs_[0] if vs_[0] != 0 and all(x == vs_[0] for x in vs_) and all(ref[f][i] for f in plan["filters"]) else 0
            sides_bad += int(ev["side"] != want)
        check(f"{key}: every vote and filter on 300 random bars, 30 days of history vs 3 years", sum(bad.values()) == 0, bad)
        whole = cfd_rules.compute(full)
        diff = {n: int(np.sum(np.asarray(whole[n]) != np.asarray(ref[n]))) for n in names}
        check(f"{key}: and on EVERY one of the {len(full):,} bars of the 3 years (whole history, same formulas)",
              sum(diff.values()) == 0, diff)
        check(f"{key}: the rule's buy/sell/none matches the search on all 300", sides_bad == 0, sides_bad)

print("2. ONLY CLOSED CANDLES - THE FORMING ONE IS NEVER READ")
idx = pd.date_range("2026-10-01 00:00", periods=400, freq="15min", tz="UTC")
rng = np.random.default_rng(1)
c = 84000 + np.cumsum(rng.normal(0, 30, len(idx)))
df = pd.DataFrame({"Open": c - 5, "High": c + 40, "Low": c - 40, "Close": c, "Volume": 100.0}, index=idx)
now = idx[-1] + pd.Timedelta(minutes=7)                  # the last candle is still forming
ev = cfd_rules.evaluate("BTC", df, now=now)
check("the reading is of the last CLOSED candle (the one before the forming one)",
      ev["ready"] and ev["bar_close"] == (idx[-2] + cfd_rules.BAR).isoformat(), ev.get("bar_close"))
df2 = df.copy(); df2.iloc[-1, df2.columns.get_loc("Close")] += 5000      # the forming candle jumps
check("...so a jump in the forming candle changes nothing", cfd_rules.evaluate("BTC", df2, now=now)["side"] == ev["side"])
check("7 minutes after a close: past the 120-second entry window", ev["fresh"] is False)
ev2 = cfd_rules.evaluate("BTC", df, now=idx[-1] + pd.Timedelta(seconds=60))
check("60 seconds after a close: fresh", ev2["fresh"] is True)
check("no rule for an Indian index", cfd_rules.evaluate("NIFTY", df, now=now) is None)
raw = cfd_rules.compute(df.iloc[:-1])
vals = ev["values"]
check("the reading carries the numbers behind the votes (RSI-2, ADX, above/below the 200 average) - for the card",
      abs(vals["rsi2"] - raw["rsi2_value"][-1]) < 1e-9 and abs(vals["adx"] - raw["adx_value"][-1]) < 1e-9
      and vals["above200"] == bool(raw["above200"][-1]) and abs(vals["sma200"] - raw["sma200"][-1]) < 1e-9
      and vals["close"] == float(df["Close"].iloc[-2]), vals)
stale = cfd_rules.evaluate("BTC", df, now=idx[-1] + cfd_rules.BAR + pd.Timedelta(seconds=20))
check("candles fetched before the last one closed (no newer candle yet): it WAITS rather than read half a candle",
      stale["ready"] is False and "waiting" in stale["why"], stale)
check("...but the card keeps the last complete reading meanwhile (it blanked every 15 minutes - the user, 4 Oct 2026),"
      " with no side and never fresh, so nothing is traded on it",
      stale.get("votes") == ev2.get("votes") and stale.get("values") == ev2.get("values") and stale["side"] == 0
      and stale["fresh"] is False and stale["ready"] is False, stale)
w_rec = cfd_rules.apply({"index": "BTC", "spot": 84000.0, "cfd_spread": 1.0}, dict(stale, side=1), "BTC")
check("...applied: no trade, the reason says waiting, the rows still carry the last reading",
      w_rec["bias"] == "NEUTRAL" and w_rec["rule"]["side"] == 0 and w_rec["blockers"][0].startswith("Waiting - waiting for")
      and [v["vote"] for v in w_rec["rule"]["votes"]] == [ev2["votes"]["rsi2"]], w_rec["blockers"])
check("fewer than 250 closed candles: not ready", cfd_rules.evaluate("BTC", df.iloc[:100], now=idx[99] + pd.Timedelta(minutes=1))["ready"] is False)

print("3. A READING BECOMES THE TESTED TICKET: STOP 3 x ATR, ONE TARGET target_r x THE STOP")
ev3 = {"ready": True, "votes": {"rsi2": -1}, "filters": {"adx25": True}, "side": -1, "atr": 100.0,
       "close": 84000.0, "bar_close": "x", "fresh": True}
rec = cfd_rules.apply({"index": "BTC", "spot": 84000.0, "cfd_spread": 5.0}, ev3, "BTC")
check("SELL: stop 84,000 + 3 x 100 = 84,300; target 84,000 - 0.2 x 300 = 83,940 (T1/T2 waypoints on the way)",
      rec["option_type"] == "PE" and rec["index_stop_loss"] == 84300.0 and rec["index_targets"] == [83980.0, 83960.0, 83940.0]
      and rec["risk_points"] == 300.0 and rec["target_basis"] == "rule", (rec["index_stop_loss"], rec["index_targets"]))
check("the Signal card gets the vote and the filter by name",
      [v["name"] for v in rec["rule"]["votes"]] == ["RSI-2 extreme"] and [v["vote"] for v in rec["rule"]["votes"]] == [-1]
      and [(f["name"], f["ok"]) for f in rec["rule"]["filters"]] == [("ADX 25+", True), ("Spread vs target", True)])
check("...and that it is a FORWARD TEST, with what the 3-year test expects",
      (rec["rule"].get("forward_test") or {}).get("since") == "2026-10-04"
      and (rec["rule"].get("forward_test") or {}).get("luck_pct") == 1.0
      and (rec["rule"].get("forward_test") or {}).get("win") == "87%", rec["rule"].get("forward_test"))
rec0 = cfd_rules.apply({"index": "BTC", "spot": 84000.0}, dict(ev3, side=0, votes={"rsi2": 0}), "BTC")
check("votes split -> no trade, no levels", rec0["bias"] == "NEUTRAL" and rec0["index_targets"] == [None] * 3
      and rec0["index_stop_loss"] is None)
# The engine's room-to-run reward:risk came along on the reading and went into the trade log as
# this rule's (17.51 on the first live rule trade, 4 Oct 2026, for a 0.75 trade).
eng = {"index": "BTC", "spot": 84000.0, "cfd_spread": 5.0, "reach_to_risk": 17.51, "reach_points": 2883.31}
r_side = cfd_rules.apply(dict(eng), ev3, "BTC")
check("the rule's reward:risk is its own - 0.2, target over stop - not the engine's room-to-run 17.51",
      r_side["reach_to_risk"] == 0.2 and r_side["reach_points"] is None, (r_side["reach_to_risk"], r_side["reach_points"]))
r_none = cfd_rules.apply(dict(eng), dict(ev3, side=0, votes={"rsi2": 0}), "BTC")
# The spread check (cfd_rsi2_loss_study.py): ATR 100 -> target 0.2 x 300 = 60 away, 20% of it = 12.
under = cfd_rules.apply({"index": "BTC", "spot": 84000.0, "cfd_spread": 11.99}, ev3, "BTC")
at_cap = cfd_rules.apply({"index": "BTC", "spot": 84000.0, "cfd_spread": 12.0}, ev3, "BTC")
unknown = cfd_rules.apply({"index": "BTC", "spot": 84000.0}, ev3, "BTC")
check("spread 11.99 on a 60-point target (under 20%): the SELL stands", under["option_type"] == "PE"
      and under["rule"]["filters"][-1]["ok"] is True and under["rule"]["filters"][-1]["limit"] == 12.0)
check("spread 12 (20% of the target): no trade, and the card's filter row says why", at_cap["bias"] == "NEUTRAL"
      and at_cap["index_targets"] == [None] * 3 and at_cap["rule"]["filters"][-1]["ok"] is False
      and at_cap["rule"]["filters"][-1]["spread"] == 12.0)
check("no spread known: no trade (never on a guess)", unknown["bias"] == "NEUTRAL" and unknown["rule"]["filters"][-1]["ok"] is False)
# The engine's reasons are not the rule's (the user's screenshot, 4 Oct 2026: "Momentum disagrees - the MACD
# histogram is 5.26 ..." and "Momentum against" over the RSI-2 rule, which has no MACD in it)
leftover = {"index": "BTC", "spot": 84775.0, "cfd_spread": 10.0, "adx_blocked": True, "macd_blocked": True,
            "not_worth_it": True, "blockers": ["Momentum disagrees - the MACD histogram is 5.26"]}
quiet = cfd_rules.apply(dict(leftover), dict(ev3, side=0, votes={"rsi2": 0}, atr=75.5), "BTC")
check("no trade: the card's reason is the rule's own - no RSI-2 setup, and the spread over its limit",
      quiet["blockers"] == ["No RSI-2 extreme on the last 15-minute close.",
                            "The spread (10.00) is 20% or more of the target - over the 9.06 limit while the market is this quiet."],
      quiet["blockers"])
check("...the engine's MACD / ADX / room flags cleared (the watchlist read 'Momentum against')",
      quiet["macd_blocked"] is False and quiet["adx_blocked"] is False and quiet["not_worth_it"] is False)
check("...and the action says the same", quiet["action"].startswith("NO TRADE - WAIT (No RSI-2 extreme on the last"))
fired = cfd_rules.apply(dict(leftover, cfd_spread=5.0), ev3, "BTC")
check("a trade: no reasons against it left on the card", fired["option_type"] == "PE" and fired["blockers"] == []
      and fired["macd_blocked"] is False)
waiting = cfd_rules.apply(dict(leftover), {"ready": False, "why": "no candles yet"}, "BTC")
check("not ready: says what it is waiting for", waiting["blockers"] == ["Waiting - no candles yet."], waiting["blockers"])
g_ev = {"ready": True, "votes": {"d1_trend": 1, "h1_trend": 1, "roc12": 1}, "filters": {"adx_rising": True, "vol_rising": True},
        "side": 1, "atr": 10.0, "close": 4000.0, "bar_close": "x", "fresh": True}
g = cfd_rules.apply({"index": "GOLD", "spot": 4000.0, "cfd_spread": 50.0}, g_ev, "GOLD")
check("gold has no spread check (its rule is unchanged): a wide spread does not stop it", g["option_type"] == "CE"
      and all(f["key"] != "spread_ok" for f in g["rule"]["filters"]))
check("...and no trade: no reward:risk at all", r_none["reach_to_risk"] is None and r_none["reach_points"] is None)
check("BTC: the RSI-2 87% forward test (cfd_strict_search.py --close) - RSI-2 with ADX >= 25, stop 3 ATR, target 0.2R",
      config.CFD_RULES["BTC"]["votes"] == ["rsi2"] and config.CFD_RULES["BTC"]["filters"] == ["adx25"]
      and config.CFD_RULES["BTC"]["stop_atr"] == 3.0 and config.CFD_RULES["BTC"]["target_r"] == 0.2)
check("GOLD: unchanged (the user's accuracy-first pick)", config.CFD_RULES["GOLD"]["votes"] == ["d1_trend", "h1_trend", "roc12"]
      and config.CFD_RULES["GOLD"]["filters"] == ["adx_rising", "vol_rising"]
      and config.CFD_RULES["GOLD"]["stop_atr"] == 3.0 and config.CFD_RULES["GOLD"]["target_r"] == 0.75
      and "forward_test" not in config.CFD_RULES["GOLD"])

print("4. THE TICKET ENGINE TRADES A RULE EXACTLY AS TESTED")
import datetime as dt
import tickets
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_now = {"t": dt.datetime(2026, 10, 5, 10, 0, 30, tzinfo=IST)}
tickets.now_ist = lambda: _now["t"]
d = tempfile.mkdtemp()
bk = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d, "t.csv"))
bk.lots = 0.1
def reading(side=1, fresh=True, bar="2026-10-05T04:30:00+00:00", spot=84000.0):
    ev = {"ready": True, "votes": {"rsi2": side}, "filters": {"adx25": True}, "side": side,
          "atr": 100.0, "close": spot, "bar_close": bar, "fresh": fresh}
    r = cfd_rules.apply({"index": "BTC", "spot": spot, "cfd_spread": 10.0, "quote_age_s": 1.0,
                         "reach_to_risk": 17.51, "reach_points": 2883.31}, ev, "BTC")      # the engine's, as live
    return r
evs = bk.update("BTC", reading())
t = bk.books["BTC"].trade
check("a fresh BUY reading opens a ticket: stop 83,700, target 84,060, one target (plain), exit at T3, rule-marked",
      any(e["kind"] == "opened" for e in evs) and t["index_sl"] == 83700.0 and t["index_targets"][2] == 84060.0
      and t["plain_exit"] and t["exit_at"] == "T3" and t["rule_strategy"] and t["cfd"], (t and (t["index_sl"], t["index_targets"])))
import trade_log
orow = [r for r in trade_log._read_rows(bk.path) if r["event"] == "OPEN"][-1]
check("the journal's OPEN row: reward_risk 0.2 (target over stop), no engine room-to-run",
      orow["reward_risk"] == "0.2" and orow["reach_points"] == "", (orow["reward_risk"], orow["reach_points"]))
q = bk.update("BTC", cfd_rules.apply({"index": "BTC", "spot": 84000.0, "quote_age_s": 1.0, "reach_to_risk": 17.51},
                                     {"ready": True, "votes": {"rsi2": 0}, "filters": {"adx25": True},
                                      "side": 0, "atr": 100.0, "bar_close": "x", "fresh": True}, "BTC"))
pt = bk.public("BTC")["ticket"]
check("the open ticket's panel keeps the rule's 0.2 after the reading goes quiet", t["reward_risk"] == 0.2
      and pt["reward_risk"] == 0.2, pt.get("reward_risk"))
bk2 = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d, "t2.csv"))
bk2._open(bk2.books["BTC"], dict(reading(), target_basis="plain_r"))
check("a ticket that is not a rule's freezes none (its panel reads the live signal, as before)",
      bk2.books["BTC"].trade["reward_risk"] is None and bk2.public("BTC")["ticket"]["reward_risk"] is None)
was = getattr(tickets.config, "EARLY_EXIT_ON_REVERSAL", False)
was_s = getattr(tickets.config, "SIGNAL_CONFIRM_SECONDS", 0)
tickets.config.EARLY_EXIT_ON_REVERSAL, tickets.config.SIGNAL_CONFIRM_SECONDS = True, 60
bk.books["BTC"].confirm_dir, bk.books["BTC"].confirm_streak, bk.books["BTC"].confirm_since = ("BEARISH", "PE"), 99, 1.0   # held since long ago
bk.tick_price("BTC", 84050.0)
check("no reversal exit on a rule's ticket (the test had none), with the reversal exit armed", t["status"] == "OPEN", t["status"])
tickets.config.EARLY_EXIT_ON_REVERSAL, tickets.config.SIGNAL_CONFIRM_SECONDS = was, was_s
bk.tick_price("BTC", 84050.0)                       # past T1 84,020 and T2 84,040, short of the target 84,060
check("T1/T2 reached: marked, the stop has NOT moved", t["hit"]["T1"] and t["hit"]["T2"] and t["index_sl"] == 83700.0)
evs = bk.tick_price("BTC", 84065.0)
check("the target: closed as a full target", "T3 hit" in t["status"], t["status"])
was_cd = tickets.config.REENTRY_COOLDOWN_MIN
tickets.config.REENTRY_COOLDOWN_MIN = 0                    # so only the one-decision-per-close guard can stop it
evs = bk.update("BTC", reading())
check("no second ticket on the SAME close (even with no cooldown)", bk.books["BTC"].trade is None
      or bk.books["BTC"].trade["status"] != "OPEN")
check("...and the card says the close is decided", bk.books["BTC"].wait_reason[1] == "NEXT CLOSE", bk.books["BTC"].wait_reason)
for g in ("_same_direction_hold", "_regime_hold", "_reward_hold", "_spread_hold", "_strike_taken", "_closed_hold"):
    setattr(bk, g, lambda *a, **k: None)                    # every engine gate open
was_rm = getattr(tickets.config, "REARM_MIN_SECONDS", 60)
tickets.config.REARM_MIN_SECONDS = 0
bk.books["BTC"].last_bias_signature = ("BULLISH", "CE")
check("a rule's reading never re-arms after a target, whatever the engine's gates say",
      bk._rearm(bk.books["BTC"], reading(bar="2026-10-05T09:00:00+00:00")) is False)
tickets.config.REARM_MIN_SECONDS, tickets.config.REENTRY_COOLDOWN_MIN = was_rm, was_cd
for g in ("_same_direction_hold", "_regime_hold", "_reward_hold", "_spread_hold", "_strike_taken", "_closed_hold"):
    delattr(bk, g)
_now["t"] += dt.timedelta(minutes=15)
bk.update("BTC", reading(bar="2026-10-05T04:45:00+00:00"))
check("the next close within 20 minutes of the exit: COOLDOWN", bk.books["BTC"].wait_reason[1] == "COOLDOWN"
      and bk.books["BTC"].trade.get("status") != "OPEN", bk.books["BTC"].wait_reason)
_now["t"] += dt.timedelta(minutes=15)
bk.update("BTC", reading(bar="2026-10-05T05:00:00+00:00", fresh=False))
check("a close older than the 2-minute window: waits for the next close", bk.books["BTC"].wait_reason[1] == "NEXT CLOSE",
      bk.books["BTC"].wait_reason)
evs = bk.update("BTC", reading(side=-1, bar="2026-10-05T05:15:00+00:00"))
t2 = bk.books["BTC"].trade
check("a fresh SELL on a new close opens: stop 84,300, target 83,940", t2["status"] == "OPEN" and t2["option_type"] == "PE"
      and t2["index_sl"] == 84300.0 and t2["index_targets"][2] == 83940.0)
evs = bk.update("BTC", reading(side=1, bar="2026-10-05T05:30:00+00:00"))
check("a position open: the next BUY close changes nothing", bk.books["BTC"].trade is t2 and t2["status"] == "OPEN"
      and bk.books["BTC"].wait_reason[1] == "POSITION OPEN")
bk.update("BTC", cfd_rules.apply({"index": "BTC", "spot": 84000.0, "quote_age_s": 1.0},
                                 {"ready": True, "votes": {"rsi2": 0}, "filters": {"adx25": True},
                                  "side": 0, "atr": 100.0, "bar_close": "x", "fresh": True}, "BTC"))
check("votes split: no trade (and the open one is left to its own exit)", bk.books["BTC"].trade is t2)

print("5. THE SIGNAL CARD - THE RULE'S OWN VOTES, IN WORDS")
import explain
w = explain.explain(reading())
check("rows are the rule's vote, its ADX filter and the spread check (no engine gate)",
      [v["name"] for v in w["votes"]] == ["RSI-2 extreme", "ADX 25+", "Spread vs target"]
      and w["votes"][2]["text"].startswith("The spread is under 20% of the target")
      and w["gate"] is None and w["votes"][0]["text"].startswith("RSI-2 is below 10 - a sharp dip")
      and w["votes"][1]["text"] == "ADX is 25 or more - a strong trend.")
check("the verdict names the decision and the exit", "BUY" in w["verdict"] and "one target at 0.2 x" in w["verdict"])
check("the levels say where the stop and the one target are", w["levels"][0].startswith("STOP 83,700")
      and "TARGET 84,060" in w["levels"][1], w["levels"])
# the numbers on the card (the user, 4 Oct 2026: "show the rsi-2 number on the card")
def card(side, r2, up, adx=31.4, spread=10.0, atr=100.0, sma=None):
    vals_ = {"rsi2": r2, "adx": adx, "above200": up}
    if sma is not None:
        vals_.update(sma200=sma, close=84000.0)
    ev_ = {"ready": True, "votes": {"rsi2": side}, "filters": {"adx25": adx >= 25}, "side": side, "atr": atr,
           "close": 84000.0, "bar_close": "x", "fresh": True, "values": vals_}
    return {v["name"]: v for v in explain.explain(cfd_rules.apply({"index": "BTC", "spot": 84000.0, "cfd_spread": spread},
                                                                    ev_, "BTC"))["votes"]}
q1 = card(0, 60.4, False)
check("no setup, below the average: '60 · sell above 90', and the words say what it waits for",
      q1["RSI-2 extreme"]["reading"] == "60 · sell above 90"
      and q1["RSI-2 extreme"]["text"] == "RSI-2 is 60.4 - not extreme. Price is below its 200-candle average, so the rule "
                                         "waits for RSI-2 above 90 (a sharp spike) to sell.", q1["RSI-2 extreme"])
q2 = card(0, 8.3, False)
check("a sharp dip but BELOW the average: not called 'not extreme' - the rule does not take dips in a downtrend",
      q2["RSI-2 extreme"]["reading"] == "8 · sell above 90" and "a sharp dip, but price is below" in q2["RSI-2 extreme"]["text"],
      q2["RSI-2 extreme"]["text"])
q3 = card(1, 6.2, True)
check("a setup: '6 · BUY', the number in the words", q3["RSI-2 extreme"]["reading"] == "6 · BUY"
      and q3["RSI-2 extreme"]["text"].endswith("(RSI-2 6.2.)"))
check("ADX shows its value: '31 · yes'", q3["ADX 25+"]["reading"] == "31 · yes", q3["ADX 25+"]["reading"])
check("the spread row shows the spread and its limit: '$10.00 / max $12.00 · yes'",
      q3["Spread vs target"]["reading"] == "$10.00 / max $12.00 · yes", q3["Spread vs target"]["reading"])
q5 = card(0, 73.9, True, sma=83962.64); p5 = q5.get("Price vs 200 avg", {})
check("the 200-candle average has its own row (the user, 4 Oct 2026): 'above 83,963 · buys only', in words too",
      p5.get("reading") == "above 83,963 · buys only" and p5.get("vote") == 1
      and p5.get("text") == "Price 84,000.00 is above its 200-candle average 83,962.64 - an uptrend, "
                                            "so the rule only buys, on sharp dips.", p5)
q6 = card(0, 60.4, False, sma=84962.0); p6 = q6.get("Price vs 200 avg", {})
check("...below it: 'below 84,962 · sells only'", p6.get("reading") == "below 84,962 · sells only"
      and p6.get("vote") == -1)
order = [v["name"] for v in explain.explain(cfd_rules.apply({"index": "BTC", "spot": 84000.0, "cfd_spread": 10.0},
         {"ready": True, "votes": {"rsi2": 0}, "filters": {"adx25": True}, "side": 0, "atr": 100.0, "close": 84000.0,
          "bar_close": "x", "fresh": True, "values": {"rsi2": 50.0, "adx": 30.0, "above200": True, "sma200": 83000.0,
          "close": 84000.0}}, "BTC"))["votes"]]
check("...right under RSI-2, before the filters", order == ["RSI-2 extreme", "Price vs 200 avg", "ADX 25+", "Spread vs target"], order)
check("...and not drawn when the average is not known yet", "Price vs 200 avg" not in card(0, 55.0, None))
q4 = card(0, 55.0, None)
check("no 200-average yet: the number alone, no direction guessed", q4["RSI-2 extreme"]["reading"] == "55 · no setup")
import feeds
class _Boom:
    def get_ohlc(self, *a, **k):
        raise RuntimeError("MetaApi rate limit (candles); backing off 30s")
class _FeedStub:
    def _provider_for(self, name, ix):
        return _Boom()
fr = feeds.CryptoFeed._cfd_rule(_FeedStub(), "BTC", {"index": "BTC", "spot": 84000.0, "cfd_spread": 5.0}) \
    if hasattr(feeds, "CryptoFeed") else None
if fr is None:
    for _cls in vars(feeds).values():
        if isinstance(_cls, type) and hasattr(_cls, "_cfd_rule"):
            fr = _cls._cfd_rule(_FeedStub(), "BTC", {"index": "BTC", "spot": 84000.0, "cfd_spread": 5.0})
            break
check("a candle failure shows its MESSAGE on the card, not only 'RuntimeError' (4 Oct 2026)",
      fr is not None and fr["blockers"][0].startswith("Waiting - no candles for the rule: RuntimeError: MetaApi rate limit (candles)"),
      fr and fr["blockers"])
pub = feeds._public(dict(reading(), technical={}, trend={}), "BTC")
check("the page gets the rule, the levels, exit at T3, and the forward-test note's facts", pub["rule"]["label"] == "RSI-2 bounce (87%)"
      and pub["exit_at"] == "T3" and pub["stop"] == 83700.0 and (pub["rule"].get("forward_test") or {}).get("trades_a_month") == 50)
nb = tickets.TicketBook(market="nse_index")
check("an Indian reading has no rule and goes the engine's way", nb.books["NIFTY"] is not None and config.cfd_rule("NIFTY") is None)

print("6. NEVER A TICKET WITHOUT A DIRECTION AND ITS LEVELS (4 Oct 2026, 12:30 IST: RSI-2 fired (a sell), the spread check")
print("   said no - and a ticket with no side, stop or target opened; Exness got an unprotected SELL)")
_now["t"] = dt.datetime(2026, 10, 6, 12, 30, 5, tzinfo=IST)
b6 = tickets.TicketBook(owner=None, market="crypto", path=os.path.join(d, "t6.csv"))
b6.lots = 0.25
ev6 = {"ready": True, "votes": {"rsi2": 1}, "filters": {"adx25": True}, "side": 1, "atr": 30.0,
       "close": 85015.0, "bar_close": "2026-10-06T07:00:00+00:00", "fresh": True,
       "values": {"rsi2": 7.0, "adx": 31.0, "above200": True}}
r6 = cfd_rules.apply({"index": "BTC", "spot": 85015.0, "cfd_spread": 10.0, "quote_age_s": 1.0}, ev6, "BTC")   # target 18: 20% = 3.6 < 10
check("the votes say BUY, the spread check says no: the reading's own side is 0, with no levels",
      r6["rule"]["side"] == 0 and r6["bias"] == "NEUTRAL" and r6["option_type"] is None and r6["index_stop_loss"] is None)
ev_out = b6.update("BTC", r6)
check("...and the ticket book opens NOTHING (it opened an empty ticket on 4 Oct)", b6.books["BTC"].trade is None
      and not any(e["kind"] == "opened" for e in ev_out), b6.books["BTC"].wait_reason)
check("...and nothing reached the journal", not os.path.exists(os.path.join(d, "t6.csv"))
      or not [r for r in trade_log._read_rows(os.path.join(d, "t6.csv")) if r["event"] == "OPEN"])
broken = dict(r6, rule=dict(r6["rule"], side=1))                     # a reading that still claims a side
b6.update("BTC", broken)
check("the ticket book's own guard: a side but no direction / stop / target -> no ticket, whatever the reading says",
      b6.books["BTC"].trade is None and "no complete set of levels" in (b6.books["BTC"].wait_reason or ("", "", ""))[2],
      b6.books["BTC"].wait_reason)
ok6 = cfd_rules.apply({"index": "BTC", "spot": 85015.0, "cfd_spread": 2.0, "quote_age_s": 1.0}, ev6, "BTC")
b6.update("BTC", ok6)
t6 = b6.books["BTC"].trade
check("the same reading with a spread under the limit: a BUY with its stop and target", t6 is not None
      and t6["option_type"] == "CE" and t6["index_sl"] is not None and t6["index_targets"][2] is not None)

print()
if fails:
    print(f"CFD RULES TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("CFD RULES TEST PASSED")
