"""Target/stop odds: an open ticket gets them, BTC is measured over a window
that means something, and the Indian numbers do not move."""
import datetime as dt, math, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, feeds, greeks, touch_model as tm

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. THE INDIAN NUMBERS DO NOT MOVE")
def old_formula(dist, spot, sigma, mins):          # the formula as it stood
    z = (dist / spot) / (sigma * math.sqrt(mins / (252.0 * 375.0)))
    return round(max(0.0, min(1.0, math.erfc(z / math.sqrt(2.0)) * 0.601)) * 100)
same = all(tm.touch_probability_pct(d, 23400, 0.13, m) == old_formula(d, 23400, 0.13, m)
           for d in (40, 100, 180, 300) for m in (30, 120, 240, 375))
check("default basis gives exactly the old percentages", same)

real_iv = greeks.implied_vol
greeks.implied_vol = lambda *a, **k: 0.40            # a fixed 40% vol for the checks below
real_mtc = tm.minutes_to_close
tm.minutes_to_close = lambda now=None: 240.0         # four hours to the bell
def chain(expiry, strike):
    return {"expiry": expiry, "strikes": [{"strike": strike, "call_ltp": 100.0, "put_ltp": 100.0}]}
nifty = {"index": "NIFTY", "spot": 24500.0, "index_targets": [24560., 24610., 24660.],
         "index_stop_loss": 24440., "option_chain": chain("2026-09-15", 24500)}
o = feeds._ladder_odds(nifty, {})
check("Nifty: window is the 15:30 close", o["horizon"] == "before the 15:30 close", o["horizon"])
check("Nifty: trading-year basis", o["year_minutes"] == tm.TRADING_YEAR_MINUTES)
check("Nifty: percentages as the old formula gives", o["t1"] == old_formula(60, 24500, 0.40, 240)
      and o["stop"] == old_formula(60, 24500, 0.40, 240), f"{o['t1']} / {o['stop']}")
tm.minutes_to_close = lambda now=None: 0.0
o = feeds._ladder_odds(nifty, {})
check("Nifty after the bell: blank, minutes 0 (page says why)", o["t1"] is None and o["minutes"] == 0)
tm.minutes_to_close = real_mtc

print("2. BTC: A WINDOW THAT MEANS SOMETHING")
check("BTC is an always-open market", config.market_for("BTC")["always_open"])
spot = 77000.0
btc = lambda expiry: {"index": "BTC", "spot": spot,
                      "index_targets": [spot * 1.01, spot * 1.02, spot * 1.03],
                      "index_stop_loss": spot * 0.985, "option_chain": chain(expiry, 77000)}
far = (dt.date.today() + dt.timedelta(days=46)).isoformat()
o = feeds._ladder_odds(btc(far), {})
check("contract weeks away: the next 24 hours", o["horizon"] == "in the next 24 hours" and o["minutes"] == 1440,
      f"{o['horizon']} / {o['minutes']}")
check("calendar-year basis", o["year_minutes"] == tm.CALENDAR_YEAR_MINUTES)
check("levels 1-3% away are not near-certain", o["t1"] is not None and o["t1"] < 90 and o["t3"] < o["t1"],
      f"T1 {o['t1']} T2 {o['t2']} T3 {o['t3']} stop {o['stop']}")
wrong = tm.touch_probability_pct(spot * 0.01, spot, 0.40, 1440, tm.TRADING_YEAR_MINUTES)
check("the trading-year basis would have overstated it", wrong > o["t1"], f"{wrong}% vs {o['t1']}%")
now_utc = dt.datetime.now(dt.timezone.utc)
soon_day = (now_utc.date() if now_utc.hour < 8 else now_utc.date() + dt.timedelta(days=1)).isoformat()
o = feeds._ladder_odds(btc(soon_day), {})
check("contract settling within a day: window runs to settlement",
      o["horizon"] == "before this contract settles at 13:30 IST" and 0 < o["minutes"] <= 1440,
      f"{o['horizon']} / {o['minutes']} min")

print("3. AN OPEN TICKET GETS ITS OWN ODDS")
idx = {"spot": 24500.0, "odds": {"t1": 1, "t2": 1, "t3": 1, "stop": 1, "minutes": 240,
                                 "iv": 40.0, "horizon": "before the 15:30 close",
                                 "year_minutes": tm.TRADING_YEAR_MINUTES}}
tk = {"open": True, "index_targets": [24560., 24610., 24660.], "index_stop": 24440.,
      "hit": {"T1": False, "T2": False, "T3": False}, "sl_hit": False}
t = feeds._ticket_odds(tk, idx)
check("priced on the ticket's frozen levels, not the live ladder's",
      t["t1"] == old_formula(60, 24500, 0.40, 240) and t["t1"] != 1, str(t))
check("carries the window for the note", t["horizon"] == "before the 15:30 close")
tk["hit"]["T1"] = True
check("a level already reached reads 100", feeds._ticket_odds(tk, idx)["t1"] == 100)
check("no volatility: blank", feeds._ticket_odds(tk, dict(idx, odds=dict(idx["odds"], iv=None)))["t2"] is None)
check("market shut (0 minutes): blank", feeds._ticket_odds(tk, dict(idx, odds=dict(idx["odds"], minutes=0)))["t2"] is None)
check("closed ticket: blank", feeds._ticket_odds(dict(tk, open=False), idx)["t2"] is None)

print("4. THE SNAPSHOT CARRIES THEM")
Feed = next(c for c in vars(feeds).values() if isinstance(c, type) and hasattr(c, "_tickets_with_odds"))
fake = types.SimpleNamespace(
    state={"indices": {"NIFTY": {"public": idx}, "SENSEX": {"public": idx}}},
    tickets=types.SimpleNamespace(public=lambda k: {"ticket": dict(tk) if k == "NIFTY" else None, "wait": None}))
snap = Feed._tickets_with_odds(fake)
check("open ticket has odds", (snap["NIFTY"]["ticket"] or {}).get("odds", {}).get("t2") is not None)
check("index with no ticket is left as it was", snap["SENSEX"]["ticket"] is None)
greeks.implied_vol = real_iv

print()
print("ODDS TEST PASSED" if not fails else f"ODDS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
