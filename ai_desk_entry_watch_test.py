#!/usr/bin/env python3
"""The AI desk's off-cycle entry watch: asked for by the user on 22 Sep 2026
("whenever its gonna needs to enter the trade it should [be] checking[,] 15
minutes may [lose] some money") - a setup used to sit unconsidered for up to
15 minutes because entries were only ever asked about at a closed candle. Now
an ADX crossing, momentum turning, a VWAP cross or an opening-range break
wakes the desk for a look the moment it happens - subject to the exact same
entry_block() limits (caps, cooldown, after-hours) as the scheduled path, plus
its own small budget so it cannot run away. Fakes only - a scripted model, a
fake feed, a clock the test moves. Nothing here trades for real."""
import datetime as dt
import os
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ai_desk as ad
import config
import market_bot as mb

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T = {"now": dt.datetime(2026, 9, 18, 10, 0, 50, tzinfo=IST), "clock": 1_789_000_000.0}


def at(h, m, s=50, day=18):
    T["now"] = dt.datetime(2026, 9, day, h, m, s, tzinfo=IST)


def chain(spot=25010.0, ce=130.0, pe=120.0):
    strikes = []
    for k in range(24500, 25550, 50):
        c = ce + (25000 - k) * 0.4
        p = pe - (25000 - k) * 0.4
        strikes.append({"strike": float(k), "call_ltp": round(max(c, 1), 2), "put_ltp": round(max(p, 1), 2),
                        "call_bid": round(max(c, 1) - 0.25, 2), "call_ask": round(max(c, 1) + 0.25, 2),
                        "put_bid": round(max(p, 1) - 0.25, 2), "put_ask": round(max(p, 1) + 0.25, 2),
                        "call_oi": 1000, "put_oi": 900})
    return {"available": True, "expiry": "2026-09-22", "spot": spot, "strikes": strikes, "pcr": 0.9}


def rec(name="NIFTY", adx_ok=None, macd=None, vwap=None, or_ready=False, or_high=25050.0, or_low=24950.0, spot=25010.0):
    return {"index": name, "spot": spot, "bias": "BULLISH", "option_type": "CE", "confidence": "Medium",
            "suggested_strike": 25000, "option_chain": chain(spot=spot),
            "technical": {"adx": 24.0, "adx_ok": adx_ok, "macd_score": macd, "vwap_gap": vwap},
            "opening_range": {"ready": or_ready, "high": or_high, "low": or_low}}


class Rules:
    lots, capital, risk_pct = 2, None, 1.0


class FakeFeed:
    def __init__(self, market="nse_index", email="me@example.invalid"):
        self.market, self.email, self.key = market, email, f"{email}#{market}"
        self.lock = threading.RLock()
        self.tickets = Rules()
        self.streamer = None
        self.dstream = None
        self.faults = []
        self.live_at = __import__("time").time()
        names = config.instruments_in(market)
        self.state = {"feed": "ok", "indices": {n: {"rec": rec(n)} for n in names}}
    def instruments(self):
        return config.instruments_in(self.market)
    def snapshot(self):
        return {"indices": {}, "tickets": {}, "session": {}}
    def _note_fault(self, where, text):
        self.faults.append((where, text))


SCRIPT, ASKED = [], []
def fake_decide(kind, index, context, desk, tools_ctx=None, client=None):
    ASKED.append((kind, index, desk))
    d = SCRIPT.pop(0) if SCRIPT else {"action": "wait", "reason": "nothing new"}
    if isinstance(d, Exception):
        raise d
    return d, {"input_tokens": 1000, "output_tokens": 100, "looked_at": ["Option chain"]}
mb.decide = fake_decide
mb.key_present = lambda: True


N = [0]
def desk(market="nse_index", email=None):
    N[0] += 1
    email = email or f"desk{N[0]}@example.invalid"          # a fresh state file every call, like ai_desk_test.py
    f = FakeFeed(market, email)
    d = ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)
    return f, d


def enter(strike=25000, side="CE", target=160.0, stop=110.0):
    return {"action": "enter", "option_type": side, "strike": strike, "target": target, "stop": stop,
            "target_confidence": 55, "reason": "The trigger was real."}


print("1. THE DETECTOR: ONE LABEL PER GENUINE CROSSING, NEVER ON THE FIRST READING")
at(10, 0, 50)
f, d = desk()
d.set_on(True, "NIFTY")
check("the first-ever reading only seeds the comparison point - nothing fires from nowhere",
      d._watch_entry("NIFTY", rec(adx_ok=False, macd=-1, vwap=-3.0)) is None and not d.pending_entry.get("NIFTY"))
d._watch_entry("NIFTY", rec(adx_ok=True, macd=-1, vwap=-3.0))          # ADX alone crosses
check("ADX crossing the gate fires trend_started, and only that",
      d.pending_entry.get("NIFTY") == [ad.ENTRY_LABELS["trend_started"]], d.pending_entry)
d.pending_entry.clear()
d._watch_entry("NIFTY", rec(adx_ok=True, macd=1, vwap=-3.0))           # macd flips -1 -> +1
check("momentum turning fires momentum_turned, and only that",
      d.pending_entry.get("NIFTY") == [ad.ENTRY_LABELS["momentum_turned"]], d.pending_entry)
d.pending_entry.clear()
d._watch_entry("NIFTY", rec(adx_ok=True, macd=1, vwap=2.0))            # vwap flips -3 -> +2
check("a VWAP cross fires vwap_crossed, and only that",
      d.pending_entry.get("NIFTY") == [ad.ENTRY_LABELS["vwap_crossed"]], d.pending_entry)
d.pending_entry.clear()
d._watch_entry("NIFTY", rec(adx_ok=True, macd=1, vwap=2.0))            # nothing changed
check("no change at all: nothing fires", not d.pending_entry.get("NIFTY"))
d._watch_entry("NIFTY", rec(adx_ok=True, macd=1, vwap=2.0, or_ready=True, spot=25100.0))  # breaks the range high
check("breaking the opening range fires range_broken", d.pending_entry.get("NIFTY") == [ad.ENTRY_LABELS["range_broken"]])
d.pending_entry.clear()
d._watch_entry("NIFTY", rec(adx_ok=True, macd=1, vwap=2.0, or_ready=True, spot=25150.0))  # still above it
check("...but only the first time it breaks - still above it a moment later fires nothing again",
      not d.pending_entry.get("NIFTY"))
d._watch_entry("NIFTY", rec(adx_ok=False, macd=-1, vwap=-4.0, or_ready=True, spot=24900.0))  # everything reverses at once
check("several genuine crossings in the same reading queue several labels",
      set(d.pending_entry.get("NIFTY", [])) == {ad.ENTRY_LABELS["momentum_turned"], ad.ENTRY_LABELS["vwap_crossed"]},
      d.pending_entry)
check("ADX falling back below the gate is not itself a trigger - only crossing UP is watched for an entry",
      ad.ENTRY_LABELS["trend_started"] not in d.pending_entry.get("NIFTY", []))
f2, d2 = desk()
d2.set_on(True, "NIFTY")
d2._watch_entry("NIFTY", rec(adx_ok=True, macd=1, vwap=2.0, or_ready=True, spot=25100.0))  # already broken out, first look ever
check("...and that includes a range already broken on the very first reading - nothing to compare against yet",
      d2._entry_state.get("NIFTY", {}).get("range_break") == "up" and not d2.pending_entry.get("NIFTY"),
      d2.pending_entry)

print("2. track() ROUTES TO THE ENTRY WATCH ONLY WHEN THERE IS NO TICKET AND THE SWITCH IS ON")
f, d = desk()
d._roll_day()                                        # settle the "new desk, first day" roll before this test builds state
d.pending_entry.clear()
d.track("NIFTY", rec(adx_ok=False, macd=0, vwap=0.0))                  # switch is off by default
check("switched off: track() never calls the entry watch, even with a real reading", not d._entry_state.get("NIFTY"))
d.set_on(True, "NIFTY")
d.track("NIFTY", rec(adx_ok=False, macd=0, vwap=0.0))
d.track("NIFTY", rec(adx_ok=True, macd=0, vwap=0.0))
check("switched on, no ticket open: track() drives the entry watch and it can fire",
      d.pending_entry.get("NIFTY") == [ad.ENTRY_LABELS["trend_started"]])
d.pending_entry.clear()
SCRIPT[:] = [enter()]
d.step()
check("once a ticket is open, track() stops feeding the entry watch", d._open_trade("NIFTY") is not None)
before = dict(d._entry_state.get("NIFTY", {}))
d.track("NIFTY", rec(adx_ok=False, macd=1, vwap=5.0))                  # would be a big crossing, if it were even looked at
check("...and does not queue an entry trigger for an index that already has one open",
      not d.pending_entry.get("NIFTY") and d._entry_state.get("NIFTY") == before)

print("3. THE OFF-CYCLE LOOK ITSELF: THE SAME LIMITS AS THE SCHEDULED ONE, JUST NOT WAITING FOR THE CANDLE")
at(10, 3, 0)                                                            # deliberately NOT a candle close
f, d = desk()
d.set_on(True, "NIFTY")
d.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["vwap_crossed"]]
ASKED.clear()
SCRIPT[:] = [{"action": "wait", "reason": "not enough on its own"}]
d._event_entries()
check("it is asked between candles, and told exactly what just happened",
      ASKED and ASKED[0][0] == "entry" and ASKED[0][1] == "NIFTY"
      and ASKED[0][2].get("what_just_happened") == [ad.ENTRY_LABELS["vwap_crossed"]], ASKED[:1])
check("a wait costs a decision but opens nothing, same as any other ask",
      d._open_trade("NIFTY") is None and d.decisions_today == 1)
check("the trigger is consumed - asking about it once is enough", not d.pending_entry.get("NIFTY"))

# Each entry_block() condition gets its own fresh desk, well clear of MAX_EVENT_ENTRIES/
# EVENT_ENTRY_GAP_S (a separate budget checked separately in section 4) so that a block
# reported here is really entry_block()'s doing, not the gap or the cap masking it.
f3, d3 = desk(); d3.set_on(True, "NIFTY")
d3.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["trend_started"]]
d3.last_exit["NIFTY"] = T["clock"]                                      # still inside COOLDOWN_MIN
d3._event_entries()
check("entry_block()'s own cooldown still applies off-cycle - not asked, nothing billed", d3.decisions_today == 0)

f4, d4 = desk(); d4.set_on(True, "NIFTY")
d4.entries["NIFTY"] = ad.MAX_ENTRIES_PER_INDEX
d4.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["trend_started"]]
d4._event_entries()
check("the per-index entry cap still applies off-cycle too", d4.decisions_today == 0)

f5, d5 = desk(); d5.set_on(True, "NIFTY")
at(15, 20, 0)                                                           # after NO_ENTRY_AFTER for an Indian index
d5.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["trend_started"]]
d5._event_entries()
check("the after-hours cutoff still applies off-cycle", d5.decisions_today == 0)
at(10, 3, 0)

f6, d6 = desk(); d6.set_on(True, "NIFTY")
SCRIPT[:] = [enter(target=170.0, stop=100.0)]
d6.step()                                                                # opens on the scheduled clock, not off-cycle
check("a trade to compare against", d6._open_trade("NIFTY") is not None)
spent = d6.decisions_today                                              # that scheduled ask already spent one
d6.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["trend_started"]]           # a stale trigger, queued after it opened
d6._event_entries()
check("a trigger for an index that already has a ticket open is discarded, never asked about",
      d6.decisions_today == spent and not d6.pending_entry.get("NIFTY"))

print("4. ITS OWN BUDGET: MAX_EVENT_ENTRIES PER INDEX A DAY, EVENT_ENTRY_GAP_S APART")
f, d = desk()
d.set_on(True, "NIFTY")
ASKED.clear()
SCRIPT[:] = [{"action": "wait", "reason": "x"}] * (ad.MAX_EVENT_ENTRIES + 2)
fired = 0
for i in range(ad.MAX_EVENT_ENTRIES + 2):
    d.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["vwap_crossed"]]
    before = len(ASKED)
    d._event_entries()
    if len(ASKED) > before:
        fired += 1
    T["clock"] += ad.EVENT_ENTRY_GAP_S + 1                              # always past the gap, so only the cap binds
check("no more than MAX_EVENT_ENTRIES off-cycle looks on one index in a day", fired == ad.MAX_EVENT_ENTRIES, fired)

f, d = desk()
d.set_on(True, "NIFTY")
ASKED.clear()
SCRIPT[:] = [{"action": "wait", "reason": "x"}] * 3
d.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["vwap_crossed"]]
d._event_entries()
n1 = len(ASKED)
T["clock"] += 5                                                         # well under EVENT_ENTRY_GAP_S
d.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["trend_started"]]
d._event_entries()
check("...and not closer together than EVENT_ENTRY_GAP_S, even for a different trigger", len(ASKED) == n1)
T["clock"] += ad.EVENT_ENTRY_GAP_S + 1
d.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["trend_started"]]
d._event_entries()
check("...but a fresh look is allowed once the gap has passed", len(ASKED) == n1 + 1)

print("5. IT NEVER SPENDS MORE THAN THE DAY'S OVERALL DECISION CAP, AND RESETS WITH THE DAY")
f, d = desk()
d.set_on(True, "NIFTY")
d.decisions_today = ad.MAX_DECISIONS_PER_DAY["nse_index"]
ASKED.clear()
d.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["trend_started"]]
d._event_entries()
check("the market-wide daily decision cap still applies off-cycle", not ASKED)
d.event_entries["NIFTY"] = 4
at(9, 30, 0, day=19)                                                   # a new day
d._roll_day()
check("a new day clears the off-cycle budget and the comparison state, not just the scheduled counters",
      d.event_entries == {} and d._entry_state == {})

print("6. step() ITSELF DISPATCHES BOTH: THE SCHEDULED CLOCK AND THE OFF-CYCLE WATCH")
f, d = desk()
d.set_on(True, "NIFTY")
at(10, 3, 0)                                                            # settle the 10:00 candle first...
SCRIPT[:] = [{"action": "wait", "reason": "nothing yet"}]
d.step()
check("...the scheduled path itself is a wait, so nothing is open going into the real test",
      d._open_trade("NIFTY") is None and d.decisions_today == 1)
d.pending_entry["NIFTY"] = [ad.ENTRY_LABELS["range_broken"]]            # still 10:03: the SAME candle as just decided
ASKED.clear()
SCRIPT[:] = [enter(target=170.0, stop=100.0)]
d.step()
check("step() takes the off-cycle look without waiting for the next candle close - the scheduled "
      "path had nothing left to say this candle, so this ask can only be the off-cycle one",
      ASKED and ASKED[0][0] == "entry" and d._open_trade("NIFTY") is not None, ASKED[:1])
check("...and the ticket it opens is a normal one - same validate(), same live premium",
      d._open_trade("NIFTY")["entry_ltp"] == 130.0)

print("7. THE PROMPT AND THE LABELS")
check("both prompts explain what wakes it early, and no longer misstate the give-back share",
      "ADX crosses the trend gate" in mb.DESK_SYSTEM and "opening range breaks" in mb.DESK_SYSTEM
      and "hands back half" not in mb.DESK_SYSTEM and "given back 70%" in mb.DESK_SYSTEM)
check("every fired label has readable text, and there are no extras nobody triggers",
      set(ad.ENTRY_LABELS) == {"trend_started", "momentum_turned", "vwap_crossed", "range_broken"})

print("AI DESK ENTRY WATCH TEST PASSED" if not fails else f"AI DESK ENTRY WATCH TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
