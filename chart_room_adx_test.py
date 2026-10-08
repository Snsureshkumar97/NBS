#!/usr/bin/env python3
"""The main index chart's two new overlays. The user, 30 Sep 2026: "draw the
room to run on the chart and add adx gate on the chart."

_candles() already sent T1/T2/T3/stop/entry as price lines and RSI/MACD as
sub-panes; it did not send ADX at all, or feeds._room()'s reachable prices
(shown so far only as text in the gauges panel, never on the chart itself).
This checks the two new payload fields _candles() now carries - the ADX
series is the SAME call signal_engine.py makes for the live gate, not a
second, possibly-different one; feeds._room() is passed straight through,
untouched, so it stays whatever the live signal actually saw.

30 Sep 2026, same day: the user caught that the ADX gate shown on the Signal
card and on the chart could disagree ("adx gate in signal card and on chart
doesnt match which is correct"). Same call, same length, same smoothing - but
feed.candles() can hand the chart a DIFFERENT df than signal_engine.py
actually analysed (the "deep" scroll-back window, refreshed on its own
slower timer, vs the continuously-updated live series). Section 2 covers the
fix: the chart's last ADX point is pinned to the recommendation's own
already-computed value on the native 15-minute view, so the number
compared to the gate always agrees with the card - never on another
timeframe, where a different reading is correct by design.

Fakes only - no Zerodha/Delta call and no socket.
"""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import math

import pandas as pd

import config
import feeds
import indicators as ind
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IDX = pd.date_range("2026-09-29 09:15", periods=40, freq="15min", tz="Asia/Kolkata")
# A genuine up-trend for most of the series, then a sharp reversal in the last
# few bars. A pure, noiseless trend saturates ADX near 100 under ANY smoothing,
# which would hide a mutation that silently swapped config.adx_dx_smoothing()
# for a fixed length (DX-over-3 and DX-over-14 would look identical on data
# that flat). The late reversal makes the two genuinely diverge: DX-over-3
# reacts to it within a few bars, DX-over-14 barely moves - so the exact
# smoothing used actually changes the number this test checks against.
CLOSE = [100.0 + i * 1.3 for i in range(32)]
CLOSE += [CLOSE[-1] - i * 1.6 for i in range(1, 9)]
BARS = pd.DataFrame({
    "Open":   [c - 0.4 for c in CLOSE],
    "High":   [c + 0.9 for c in CLOSE],
    "Low":    [c - 0.9 for c in CLOSE],
    "Close":  CLOSE,
    "Volume": [1000 + i * 7 for i in range(40)],
}, index=IDX)


def mk_rec(spot=151.0, reach_up=120.0, reach_down=60.0, cap_up="a normal day's range",
           cap_down="a normal day's range", option_type="CE",
           t1=None, t2=None, t3=None, stop=None, adx=None):
    return {
        "index": "NIFTY", "bias": "bullish", "action": "BUY", "confidence": 72,
        "spot": spot, "suggested_strike": 25000, "option_type": option_type,
        "index_targets": [t1, t2, t3] if t1 is not None else None,
        "index_stop_loss": stop,
        "reach": {"reach_up": reach_up, "cap_up": cap_up,
                  "reach_down": reach_down, "cap_down": cap_down},
        "reach_points": reach_up, "reach_to_risk": 1.4,
        "risk_points": 40, "checks": {}, "option_chain": {}, "adx": adx,
    }


_UNSET = object()

class FakeFeed:
    def __init__(self, df=BARS, rec=_UNSET, alt=_UNSET):
        self.df = df
        self.rec = mk_rec() if rec is _UNSET else rec
        self.alt = alt      # what feed.ohlc() hands back for a non-15m timeframe
    def candles(self, key):
        return self.df, self.rec
    def ohlc(self, key, tf):
        return BARS if self.alt is _UNSET else self.alt


def call(feed=None, market="nse_index", qs=None):
    feeds.for_user = lambda email, mkt=None, start=True: (feed or FakeFeed())
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    h._candles("someone@example.com", "NIFTY", qs or {})
    return json.loads(out["body"])


_real_for_user = feeds.for_user

print("1. ADX - THE SAME CALL THE LIVE SIGNAL MAKES")
p = call()
n = len(p["candles"])
check("one ADX reading per candle sent", isinstance(p["adx"], list) and len(p["adx"]) == n, len(p.get("adx") or []))
check("every reading is a finite number, never NaN/inf leaking through as JSON",
      all(v is None or (isinstance(v, (int, float)) and math.isfinite(v)) for v in p["adx"]), p["adx"][:5])
check("the warm-up window is 0, not null - indicators.adx() itself fills NaN with 0, clean() only strips real NaN/inf",
      p["adx"][0] == 0, p["adx"][0])
expect = ind.adx(BARS, config.ADX_LENGTH, config.adx_dx_smoothing("nse_index"))
check("the LAST value matches indicators.adx() run directly with the live signal's own length/smoothing - not a second, possibly-different computation",
      p["adx"][-1] == round(float(expect.iloc[-1]), 2), (p["adx"][-1], round(float(expect.iloc[-1]), 2)))
check("adx_len is the config length the pane's label reads", p["adx_len"] == config.ADX_LENGTH)

print("2. THE READING AGAINST THE GATE MUST MATCH THE SIGNAL CARD, EVEN WHEN THE CHART'S OWN DF IS STALE")
# feed.candles() can hand back the "deep" scroll-back window (feeds.py's
# self.hist), refreshed on its own slower 4-minute timer via a separate REST
# pull - not the same series entry["df"] signal_engine.py actually analysed.
# Recomputed on that different data, the line's own last point can land off
# the number that decided PASS/WEAK. The user, 30 Sep 2026: "adx gate in
# signal card and on chart doesnt match which is correct" - the signal card
# is: it is what the gate actually used, so the chart's last point must be
# pinned to it, not to whatever recomputing on a possibly-stale df produces.
STALE_ADX = 55.0    # deliberately far from whatever BARS itself would produce
p = call(FakeFeed(rec=mk_rec(adx=STALE_ADX)))
check("the chart's last ADX point is pinned to the recommendation's own value on the native 15m view",
      p["adx"][-1] == STALE_ADX, p["adx"][-1])
check("...and it is not just coincidence - recomputing straight from this df gives something else entirely",
      round(float(ind.adx(BARS, config.ADX_LENGTH, config.adx_dx_smoothing("nse_index")).iloc[-1]), 2) != STALE_ADX)
check("only the LAST point is pinned - the rest of the line still shows the chart's own df, not flattened to one number",
      p["adx"][-2] != STALE_ADX, p["adx"][-5:])

print("2b. A DIFFERENT TIMEFRAME IS A DIFFERENT READING BY DESIGN - NOT PINNED")
# The gate only ever runs on the 15-minute series. A 5-minute or hourly ADX is
# already a legitimately different number from the 15m gate's, the same way
# RSI/MACD/EMA already are on those views with no attempt to reconcile them -
# pinning here would silently overwrite a genuinely different, correct reading.
p = call(FakeFeed(rec=mk_rec(adx=STALE_ADX)), qs={"tf": ["5m"]})
check("on a 5-minute view the last point is left as computed, not pinned to the 15m recommendation's value",
      p["adx"][-1] != STALE_ADX, p["adx"][-1])
check("the interval actually reflects the 5m request", p["interval"] == "5m", p["interval"])

print("2c. NO adx ON THE RECOMMENDATION AT ALL - NOTHING TO PIN TO, LEFT AS COMPUTED")
p = call(FakeFeed(rec=mk_rec(adx=None)))
check("with no adx on the recommendation, the last point is simply whatever the df produced - no crash, no null",
      p["adx"][-1] is not None)

print("3. THE GATE LINE'S VALUE FOLLOWS SIGNAL_STRICTNESS, NOT A HARD-CODED NUMBER")
old = config.SIGNAL_STRICTNESS
try:
    for mode, expect_gate in (("strict", 20), ("balanced", 18), ("loose", 15)):
        config.SIGNAL_STRICTNESS = mode
        p = call()
        check(f"{mode}: adx_gate is {expect_gate} (config.strictness()['adx'])", p["adx_gate"] == expect_gate, p["adx_gate"])
finally:
    config.SIGNAL_STRICTNESS = old

print("4. ROOM TO RUN - PASSED THROUGH FROM feeds._room(), NOT RE-DERIVED")
p = call()
expect_room = feeds._room(mk_rec())
check("room is exactly what feeds._public()/_room() computed for this rec - both directions, the price each reaches, what limits it",
      p["room"] == expect_room, (p["room"], expect_room))
check("up_to is spot + the up reach - a real price, not just a point count", p["room"]["up_to"] == 151.0 + 120.0, p["room"])
check("down_to is spot - the down reach", p["room"]["down_to"] == 151.0 - 60.0, p["room"])
check("the side follows the option type actually suggested (CE here)", p["room"]["side"] == "up", p["room"]["side"])

print("5. A PE SIGNAL: ROOM STILL CARRIES BOTH DIRECTIONS, SIDE FLIPS")
p = call(FakeFeed(rec=mk_rec(option_type="PE")))
check("side flips to down for a PE, but up_to/down_to are both still there - a PE trader still sees room the other way",
      p["room"]["side"] == "down" and p["room"]["up_to"] is not None and p["room"]["down_to"] is not None, p["room"])

print("6. NO SIGNAL AT ALL - ROOM DEGRADES QUIETLY, NOT WITH AN ERROR")
p = call(FakeFeed(rec=None))
check("candles.rec is falsy: _candles() falls back to pub={} - room, levels, action all None, not a crash",
      p["room"] is None and p["levels"] == {"t1": None, "t2": None, "t3": None, "stop": None, "spot": None}
      and p["action"] is None, (p["room"], p["levels"], p["action"]))
check("...but the candles and ADX line are unaffected - the chart itself does not depend on there being a signal",
      len(p["candles"]) == n and p["adx"][-1] is not None, (len(p["candles"]), p["adx"][-1]))

print("7. A BROKEN ADX CALL DOES NOT TAKE THE REST OF THE CHART DOWN WITH IT")
real_adx = ind.adx
ind.adx = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    p = call()
    check("adx is None, not an exception, and not silently omitted from the payload", "adx" in p and p["adx"] is None, p.get("adx"))
    check("...but everything else - candles, rsi, macd, room, levels - is still there, same as any other indicator's own try/except",
          len(p["candles"]) == n and p["rsi"] is not None and p["room"] is not None and p["levels"]["spot"] == 151.0,
          (len(p["candles"]), p["rsi"] is not None, p["room"] is not None, p["levels"]))
finally:
    ind.adx = real_adx

print("8. adx_gate IS STILL SENT EVEN WHEN THE ADX SERIES ITSELF FAILED")
ind.adx = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    p = call()
    check("the gate threshold does not depend on the series computation succeeding - they are independent lookups",
          p["adx_gate"] == config.strictness()["adx"], p["adx_gate"])
finally:
    ind.adx = real_adx

feeds.for_user = _real_for_user

print("9. THE FRONTEND: STRUCTURE (since 8 Oct 2026 the chart is TradingView's Lightweight Charts: panes and series)")
SRC = open("web_server.py").read()
start = SRC.index("// THE CHART")
chart = SRC[start:SRC.index('cv.addEventListener("dblclick"', start)]
check("a third sub-pane joins RSI/MACD, stacked under them - on the Chart tab; beside the Signal card the chart is price-only",
      "}), 1);" in chart and "}), 2);" in chart and "}), 3);" in chart and 's.adx = chart.addSeries(LW.LineSeries' in chart
      and 'if(mode === "full"){' in chart and 'const mode = TAB === "signal" ? "price" : "full";' in chart)
check("each indicator pane sized the same, the price pane most of the height",
      "pn.setStretchFactor(i === 0 ? 5 : 1)" in chart)
check("the pane's value is labelled, same convention as RSI / MACD", 'color: C.adx, title: "ADX"' in chart
      and 'title: "RSI"' in chart and 'title: "MACD"' in chart)
check("the gate is a reference line at the THRESHOLD, not at a hard-coded 20 - it reads d.adx_gate, matching the value the backend actually sent",
      "createPriceLine({price: d.adx_gate, color: C.warn" in chart)
check("the gate line uses the warning colour, the ADX line its own - two different meanings, two different colours",
      "color: C.warn" in chart.split("LWC.gate = d.adx_gate")[1][:300] and "color: C.adx" in chart)
check("the floor under the gate keeps it on screen even on a dead-quiet day, rather than the pane's scale collapsing to whatever ADX happens to be",
      "Math.max(g * 1.5," in chart and "minValue: 0, maxValue: hi" in chart)

print("10. THE FRONTEND: ROOM TO RUN ON THE PRICE PANE")
lv = chart[chart.index("function chLevels("):]
lv = lv[:lv.index("\n}\n")]
check("room reads live off d.room, not frozen to an open ticket like L (T1/T2/T3/stop) is",
      "const R = (d && d.room) || {};" in lv)
r_line = [ln for ln in lv.splitlines() if "const R = (d && d.room)" in ln][0]
check("...and that line does not reference the open-ticket object at all", "TK" not in r_line, r_line)
check("both reach prices are pulled into the auto-range, with the same squash-guard as T1/T2/T3/stop/entry",
      "LWC.levelVals = lv.map(a => a.v);" in chart and "chWiden(original(), LWC.levelVals)" in chart)
check("both directions are in the SAME level list T1/T2/T3/SL/Entry are - one way of drawing them, not a second one",
      '[R.up_to, "Room ↑", C.up, "dot"]' in lv and '[R.down_to, "Room ↓", C.down, "dot"]' in lv)
check("room's line style is visually distinct from a trade level's - a finer dotted line, not just a different colour of the same dash",
      'lineStyle: a.style === "dot" ? LS.SparseDotted : LS.Dashed' in chart)

print("11. THE FRONTEND: A FOURTH THEME COLOUR, DECLARED EVERYWHERE THE OTHERS ARE")
themes = [ln for ln in SRC.splitlines() if "--rsi:" in ln]
check("every theme block that declares --rsi also declares --adx - dark, kite light, kite dark",
      len(themes) >= 3 and all("--adx:" in ln for ln in themes), themes)
check("the chart's palette actually reads it (not just declared in CSS and never used)", "adx: css(\"--adx\")" in chart)
check("the legend names it too, the same way RSI/MACD already do", 'd.adx && TAB !== "signal" ? `<span class="o">ADX' in chart)

print("CHART ROOM+ADX TEST PASSED" if not fails else f"CHART ROOM+ADX TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
