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
           t1=None, t2=None, t3=None, stop=None):
    return {
        "index": "NIFTY", "bias": "bullish", "action": "BUY", "confidence": 72,
        "spot": spot, "suggested_strike": 25000, "option_type": option_type,
        "index_targets": [t1, t2, t3] if t1 is not None else None,
        "index_stop_loss": stop,
        "reach": {"reach_up": reach_up, "cap_up": cap_up,
                  "reach_down": reach_down, "cap_down": cap_down},
        "reach_points": reach_up, "reach_to_risk": 1.4,
        "risk_points": 40, "checks": {}, "option_chain": {},
    }


_UNSET = object()

class FakeFeed:
    def __init__(self, df=BARS, rec=_UNSET):
        self.df = df
        self.rec = mk_rec() if rec is _UNSET else rec
    def candles(self, key):
        return self.df, self.rec


def call(feed=None, market="nse_index"):
    feeds.for_user = lambda email, mkt=None, start=True: (feed or FakeFeed())
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    h._candles("someone@example.com", "NIFTY", {})
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

print("2. THE GATE LINE'S VALUE FOLLOWS SIGNAL_STRICTNESS, NOT A HARD-CODED NUMBER")
old = config.SIGNAL_STRICTNESS
try:
    for mode, expect_gate in (("strict", 20), ("balanced", 18), ("loose", 15)):
        config.SIGNAL_STRICTNESS = mode
        p = call()
        check(f"{mode}: adx_gate is {expect_gate} (config.strictness()['adx'])", p["adx_gate"] == expect_gate, p["adx_gate"])
finally:
    config.SIGNAL_STRICTNESS = old

print("3. ROOM TO RUN - PASSED THROUGH FROM feeds._room(), NOT RE-DERIVED")
p = call()
expect_room = feeds._room(mk_rec())
check("room is exactly what feeds._public()/_room() computed for this rec - both directions, the price each reaches, what limits it",
      p["room"] == expect_room, (p["room"], expect_room))
check("up_to is spot + the up reach - a real price, not just a point count", p["room"]["up_to"] == 151.0 + 120.0, p["room"])
check("down_to is spot - the down reach", p["room"]["down_to"] == 151.0 - 60.0, p["room"])
check("the side follows the option type actually suggested (CE here)", p["room"]["side"] == "up", p["room"]["side"])

print("4. A PE SIGNAL: ROOM STILL CARRIES BOTH DIRECTIONS, SIDE FLIPS")
p = call(FakeFeed(rec=mk_rec(option_type="PE")))
check("side flips to down for a PE, but up_to/down_to are both still there - a PE trader still sees room the other way",
      p["room"]["side"] == "down" and p["room"]["up_to"] is not None and p["room"]["down_to"] is not None, p["room"])

print("5. NO SIGNAL AT ALL - ROOM DEGRADES QUIETLY, NOT WITH AN ERROR")
p = call(FakeFeed(rec=None))
check("candles.rec is falsy: _candles() falls back to pub={} - room, levels, action all None, not a crash",
      p["room"] is None and p["levels"] == {"t1": None, "t2": None, "t3": None, "stop": None, "spot": None}
      and p["action"] is None, (p["room"], p["levels"], p["action"]))
check("...but the candles and ADX line are unaffected - the chart itself does not depend on there being a signal",
      len(p["candles"]) == n and p["adx"][-1] is not None, (len(p["candles"]), p["adx"][-1]))

print("6. A BROKEN ADX CALL DOES NOT TAKE THE REST OF THE CHART DOWN WITH IT")
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

print("7. adx_gate IS STILL SENT EVEN WHEN THE ADX SERIES ITSELF FAILED")
ind.adx = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    p = call()
    check("the gate threshold does not depend on the series computation succeeding - they are independent lookups",
          p["adx_gate"] == config.strictness()["adx"], p["adx_gate"])
finally:
    ind.adx = real_adx

feeds.for_user = _real_for_user

print("8. THE FRONTEND: STRUCTURE (chart_label_test.py's own approach to this function - it draws on a real <canvas>, not something a Python test executes)")
SRC = open("web_server.py").read()
start = SRC.index("function chartDraw()")
chart = SRC[start:SRC.index("function chartZoom(", start)]
check("a third sub-pane joins RSI/MACD, sized and stacked the same way", "hasAdx = !!d.adx" in chart and "subCount = (hasRsi?1:0) + (hasMacd?1:0) + (hasAdx?1:0)" in chart)
check("it gets its own top offset in the same stacking chain as rsiTop/macdTop", "adxTop = null" in chart and "if(hasAdx){ nextTop += paneGap; adxTop = nextTop;" in chart)
check("the pane is labelled with the live length, same convention as 'RSI 14' / 'MACD 12,26,9'", 'paneLabel(adxTop, `ADX ${d.adx_len' in chart)
check("the gate is a reference line at the THRESHOLD, not at a hard-coded 20 - it reads d.adx_gate, matching the value the backend actually sent",
      "adxY(d.adx_gate)" in chart and "d.adx_gate" in chart)
adx_block = chart.split("if(adxTop")[1].split("line(d.adx, C.adx, null, adxY);")[0]
check("the gate line and its number use the warning colour, not the ADX line's own colour - two different meanings, two different colours",
      "cx.strokeStyle = C.warn" in adx_block and "cx.fillStyle = C.warn" in adx_block, adx_block)
check("the ADX line itself is drawn in its own colour, not reused from another series", "line(d.adx, C.adx, null, adxY)" in chart)
check("the floor under the gate keeps it on screen even on a dead-quiet day, rather than the pane's scale collapsing to whatever ADX happens to be",
      "aMax = Math.max((d.adx_gate||20) * 1.5" in chart)

print("9. THE FRONTEND: ROOM TO RUN ON THE PRICE PANE")
check("room reads live off d.room, not frozen to an open ticket like L (T1/T2/T3/stop) is",
      "const R = d.room || {};" in chart)
r_line = [ln for ln in chart.splitlines() if "const R = d.room" in ln][0]
check("...and that line does not reference the open-ticket object at all", "TK" not in r_line, r_line)
check("both reach prices are pulled into the auto-range calc, same squash-guard as T1/T2/T3/stop/entry already use",
      "R.up_to,R.down_to" in chart.replace(" ", ""))
check("both directions are added to the SAME tag-drawing array T1/T2/T3/SL/Entry already use - one placement algorithm, not a second one",
      '"Room ↑", C.up, [2,3]' in chart and '"Room ↓", C.down, [2,3]' in chart)
check("room's line style is visually distinct from a trade level's - a finer dotted line, not just a different colour of the same dash",
      "dash: a[3]||[5,4]" in chart and "cx.setLineDash(a.dash)" in chart)

print("10. THE FRONTEND: A FOURTH THEME COLOUR, DECLARED EVERYWHERE THE OTHERS ARE")
themes = [ln for ln in SRC.splitlines() if "--rsi:" in ln]
check("every theme block that declares --rsi also declares --adx - dark, kite light, kite dark",
      len(themes) >= 3 and all("--adx:" in ln for ln in themes), themes)
check("the canvas palette actually reads it (not just declared in CSS and never used)", "adx: css(\"--adx\")" in chart)
check("the legend names it too, the same way RSI/MACD already do", 'd.adx ? `<span class="o">ADX' in chart)

print("CHART ROOM+ADX TEST PASSED" if not fails else f"CHART ROOM+ADX TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
