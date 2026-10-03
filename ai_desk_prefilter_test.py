#!/usr/bin/env python3
"""_worth_asking(): the AI desk's free pre-check before it ever pays for a model call (28 Sep 2026).

The user: "everytime it gives me explanations but doest actual enters any trades always say wait
... even after it does again it will give me any other reason." Read off the live account's own
decision log: 33/33 decisions were "wait", and neither the scheduled per-candle path nor the
event-driven one checked whether there was even a plausible case before asking - on Bitcoin's 24/7
clock the scheduled path alone can ask up to 96 times a day regardless of the rule engine's own
view. This pins _worth_asking() itself (a marginal ADX crossing, chasing a move already run most
of the day's range, taker flow actively disagreeing - the same reasons the model gave, over and
over, in its own real wait answers) and that step()/_event_entries() actually skip the model call
when it says no - never touching entry_block()'s own limits or the event-entry counters when
that happens.

Fakes only - a fake feed, a fake model that records every ask, a clock the test moves.
"""
import datetime as dt
import os
os.environ["NBS_CRYPTO_VENUE"] = "delta"   # checks the Delta venue - the rollback path (config.CRYPTO_VENUE; Exness is exness_switch_test.py)
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_desk as ad
import config
import market_bot as mb
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T = {"now": dt.datetime(2026, 9, 28, 10, 0, 50, tzinfo=IST), "clock": 1_790_000_000.0}
tickets.now_ist = lambda: T["now"]      # book.entry_block()'s own session gates read this directly

GATE = config.strictness().get("adx", config.ADX_TREND_THRESHOLD)


def rec(bias="BULLISH", adx=None, range_pos=None):
    """A minimal but real-shaped rec: rec["technical"]["adx"] and
    rec["trend"]["range_pos_pct"] are the same fields feeds.py actually sets."""
    return {"index": "BTC", "bias": bias, "option_type": "CE" if bias == "BULLISH" else "PE",
            "spot": 83000.0, "technical": {"adx": adx}, "trend": {"range_pos_pct": range_pos}}


class Rules:
    lots, capital, risk_pct = 1, None, 1.0


_N = [0]


class FakeFeed:
    """taker_flow is a plain method returning whatever the test wants (a cvd_15m_pct via
    taker_flow.brief's own field name), matching feeds.py's real shape closely enough for
    taker_flow.brief() to read it - see taker_flow.py's own tests for that function itself.

    Each instance gets its own email, so its AIDesk gets its own state_path - unless a test
    is deliberately checking what a SHARED file does (a restart), separate desk() calls must
    not collide on the same on-disk state, or an earlier call's last_candle/event_entries would
    silently gate a later, unrelated one."""
    def __init__(self, cvd_15m_pct=None, has_taker_flow=True):
        _N[0] += 1
        self.market, self.email = "crypto", f"prefilter-test-{_N[0]}@example.invalid"
        self.key = f"{self.email}#{self.market}"
        self.lock = threading.RLock()
        self.tickets = Rules()
        self.faults = []
        self.live_at = __import__("time").time()
        self._cvd = cvd_15m_pct
        if has_taker_flow:
            self.taker_flow = self._taker_flow
        self.state = {"feed": "ok", "indices": {"BTC": {"rec": rec()}}}
    def instruments(self):
        return ["BTC"]
    def snapshot(self):
        return {"indices": {}, "tickets": {}, "session": {}}
    def _note_fault(self, where, text):
        self.faults.append((where, text))
    def _taker_flow(self, index):
        if self._cvd is None:
            return None
        return {"windows": {"15m": {"cvd_pct_of_volume": self._cvd, "price_change_pct": 0.1, "complete": True},
                            "5m": {"cvd_pct_of_volume": self._cvd}},
                "live": True, "tape_covers_minutes": 15.0}


def desk(cvd_15m_pct=None, has_taker_flow=True):
    f = FakeFeed(cvd_15m_pct=cvd_15m_pct, has_taker_flow=has_taker_flow)
    return f, ad.AIDesk(f, now=lambda: T["now"], clock=lambda: T["clock"], start=False)


print("1. NO VIEW AT ALL - NEUTRAL OR MISSING BIAS IS NEVER WORTH ASKING ABOUT")
f, d = desk()
check("NEUTRAL bias", d._worth_asking("BTC", rec(bias="NEUTRAL", adx=30.0, range_pos=50.0)) is False)
check("no rec at all", d._worth_asking("BTC", None) is False)
check("a bias the engine never actually uses", d._worth_asking("BTC", rec(bias="SIDEWAYS", adx=30.0)) is False)

print("2. A MARGINAL ADX CROSSING - THE MODEL'S OWN MOST COMMON COMPLAINT")
check(f"exactly at the gate ({GATE}) - filtered", d._worth_asking("BTC", rec(adx=GATE, range_pos=50.0)) is False)
check(f"{ad.PREFILTER_ADX_MARGIN - 0.1} above the gate - still filtered",
      d._worth_asking("BTC", rec(adx=GATE + ad.PREFILTER_ADX_MARGIN - 0.1, range_pos=50.0)) is False)
check(f"{ad.PREFILTER_ADX_MARGIN} clear of the gate - now worth asking",
      d._worth_asking("BTC", rec(adx=GATE + ad.PREFILTER_ADX_MARGIN + 1.0, range_pos=50.0)) is True)
check("ADX missing entirely - filtered, not guessed", d._worth_asking("BTC", rec(adx=None, range_pos=50.0)) is False)

print("3. CHASING A MOVE ALREADY MOST OF THE WAY THROUGH THE DAY'S RANGE")
strong_adx = GATE + ad.PREFILTER_ADX_MARGIN + 5.0
check("BULLISH at the top of the day's range - filtered",
      d._worth_asking("BTC", rec(bias="BULLISH", adx=strong_adx, range_pos=ad.PREFILTER_MAX_RANGE_POS)) is False)
check("BULLISH comfortably below that - worth asking",
      d._worth_asking("BTC", rec(bias="BULLISH", adx=strong_adx, range_pos=ad.PREFILTER_MAX_RANGE_POS - 20)) is True)
check("BEARISH at the bottom of the day's range - filtered (mirrored)",
      d._worth_asking("BTC", rec(bias="BEARISH", adx=strong_adx,
                                  range_pos=100 - ad.PREFILTER_MAX_RANGE_POS)) is False)
check("BEARISH comfortably above that - worth asking",
      d._worth_asking("BTC", rec(bias="BEARISH", adx=strong_adx,
                                  range_pos=100 - ad.PREFILTER_MAX_RANGE_POS + 20)) is True)
check("range position missing - not blocked by this check alone",
      d._worth_asking("BTC", rec(bias="BULLISH", adx=strong_adx, range_pos=None)) is True)

print("4. TAKER FLOW ACTIVELY DISAGREEING WITH THE SIDE (BITCOIN ONLY)")
f2, d2 = desk(cvd_15m_pct=-ad.PREFILTER_TAKER_FLOW_VETO - 5.0)
check("BULLISH into 15m sellers past the veto - filtered",
      d2._worth_asking("BTC", rec(bias="BULLISH", adx=strong_adx, range_pos=50.0)) is False)
f3, d3 = desk(cvd_15m_pct=ad.PREFILTER_TAKER_FLOW_VETO + 5.0)
check("BEARISH into 15m buyers past the veto - filtered (mirrored)",
      d3._worth_asking("BTC", rec(bias="BEARISH", adx=strong_adx, range_pos=50.0)) is False)
f4, d4 = desk(cvd_15m_pct=3.0)      # mild, same side or too small to veto
check("flow only mildly against, under the veto size - still worth asking",
      d4._worth_asking("BTC", rec(bias="BEARISH", adx=strong_adx, range_pos=50.0)) is True)
f5, d5 = desk(cvd_15m_pct=None)
check("no taker flow reading at all - not blocked by this check alone",
      d5._worth_asking("BTC", rec(bias="BULLISH", adx=strong_adx, range_pos=50.0)) is True)

print("5. AN INDEX FEED WITH NO taker_flow METHOD AT ALL (the NSE indices) DOES NOT CRASH")
f6, d6 = desk(has_taker_flow=False)
check("skips the flow check cleanly, judges on ADX/range alone",
      d6._worth_asking("BTC", rec(bias="BULLISH", adx=strong_adx, range_pos=50.0)) is True)

print("6. A GENUINELY STRONG CASE - EVERYTHING CLEAR - IS NEVER FILTERED")
f7, d7 = desk(cvd_15m_pct=25.0)     # flow agreeing, not just neutral
check("strong ADX, mid-range, flow agreeing - worth asking",
      d7._worth_asking("BTC", rec(bias="BULLISH", adx=strong_adx, range_pos=50.0)) is True)

print("7. WIRED IN: step()'s SCHEDULED PATH SKIPS THE MODEL WHEN THE FILTER SAYS NO")
ASKED = []
def fake_decide(kind, index, context, desk_, tools_ctx=None, client=None):
    ASKED.append((kind, index, T["clock"]))
    return {"action": "wait", "reason": "irrelevant to this test"}, {"input_tokens": 1, "output_tokens": 1, "looked_at": []}
mb.decide = fake_decide
mb.key_present = lambda: True

def now_open(now, name):
    return True
ad.is_market_open = now_open

f8, d8 = desk(cvd_15m_pct=None)
d8.enabled = {"BTC": True}
f8.state["indices"]["BTC"]["rec"] = rec(bias="NEUTRAL", adx=None)    # nothing plausible at all
before = len(ASKED)
d8.step()
check("a NEUTRAL scheduled candle never reaches the model", len(ASKED) == before, ASKED)

f9, d9 = desk(cvd_15m_pct=None)
d9.enabled = {"BTC": True}
f9.state["indices"]["BTC"]["rec"] = rec(bias="BULLISH", adx=strong_adx, range_pos=50.0)
before = len(ASKED)
d9.step()
check("a genuinely plausible scheduled candle DOES reach the model", len(ASKED) == before + 1, ASKED)

print("8. WIRED IN: _event_entries() SKIPS THE MODEL, AND DOES NOT SPEND THE DAY'S EVENT-LOOK BUDGET")
f10, d10 = desk(cvd_15m_pct=None)
d10.enabled = {"BTC": True}
f10.state["indices"]["BTC"]["rec"] = rec(bias="NEUTRAL", adx=None)
with d10.lock:
    d10.pending_entry["BTC"] = ["Price crossed VWAP"]
before, before_n = len(ASKED), d10.event_entries.get("BTC", 0)
d10._event_entries()
check("a NEUTRAL off-cycle trigger never reaches the model", len(ASKED) == before, ASKED)
check("...and does not spend one of today's event-look slots either",
      d10.event_entries.get("BTC", 0) == before_n, d10.event_entries)

f11, d11 = desk(cvd_15m_pct=None)
d11.enabled = {"BTC": True}
f11.state["indices"]["BTC"]["rec"] = rec(bias="BULLISH", adx=strong_adx, range_pos=50.0)
with d11.lock:
    d11.pending_entry["BTC"] = ["ADX crossed above the trend gate"]
before = len(ASKED)
d11._event_entries()
check("a genuinely plausible off-cycle trigger DOES reach the model, and now spends a slot",
      len(ASKED) == before + 1 and d11.event_entries.get("BTC") == 1, (ASKED, d11.event_entries))

print("AI DESK PRE-FILTER TEST PASSED" if not fails else f"AI DESK PRE-FILTER TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
