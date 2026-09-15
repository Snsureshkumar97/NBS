"""The custom screener: the arithmetic, the checks, the presets, saved screens."""
import json, math, os, sys, tempfile
os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import indicators as ind, screener as sc

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

def daily(closes, vols=None, start="2025-08-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.array(closes, float)
    return pd.DataFrame({"ts": idx.tz_localize("Asia/Kolkata"), "open": c * 0.998, "high": c * 1.006,
                         "low": c * 0.994, "close": c, "volume": vols if vols is not None else np.full(len(c), 1e6)})

print("1. THE ARITHMETIC")
rng = np.random.default_rng(3)
closes = 100 * np.cumprod(1 + rng.normal(0.0005, 0.012, 280))
fr = sc.frames(daily(closes))
d = fr["daily"]["Close"]
val = lambda o: sc._value(fr, sc._clean_operand(o, "rhs" if "mult" in o else "lhs"), {})
check("SMA(20) = mean of the last 20 closes", abs(val(sc._i("sma", period=20)) - d.iloc[-20:].mean()) < 1e-9)
w = np.arange(1, 21)
check("WMA(20) weights the latest bar most", abs(val(sc._i("wma", period=20)) - np.dot(d.iloc[-20:], w) / w.sum()) < 1e-9)
check("EMA and RSI are TradePicker's own", abs(val(sc._i("ema", period=50)) - ind.ema(d, 50).iloc[-1]) < 1e-9
      and abs(val(sc._i("rsi", period=14)) - ind.rsi(d, 14).iloc[-1]) < 1e-9)
mid, sd = d.iloc[-20:].mean(), d.iloc[-20:].std()
check("Bollinger upper = mean + 2 standard deviations", abs(val(sc._i("bb_high", period=20, stdev=2)) - (mid + 2 * sd)) < 1e-9)
check("bars ago reads an older bar", val(sc._i("close", offset=3)) == d.iloc[-4])
check("multiplier and added amount apply to the right side", abs(val(dict(sc._i("close"), mult=1.5, add=2)) - (d.iloc[-1] * 1.5 + 2)) < 1e-9)
wk = fr["weekly"]["Close"]
check("weekly candles end on Fridays with the week's last close", wk.index[-2].weekday() == 4 and wk.iloc[-2] == d[d.index <= wk.index[-2]].iloc[-1])
check("monthly candles exist for the months covered", 12 <= len(fr["monthly"]) <= 14, len(fr["monthly"]))
check("a smoothed indicator is 'not known' before it has enough bars", sc._series(sc.frames(daily(closes[:30])), sc._clean_operand(sc._i("ema", period=50), "lhs"), {}).isna().all())

print("1b. CCI, WILLIAMS %R, PARABOLIC SAR")
hi_, lo_ = fr["daily"]["High"], fr["daily"]["Low"]
tp = ((hi_ + lo_ + d) / 3).iloc[-20:]
cci_ref = (tp.iloc[-1] - tp.mean()) / (0.015 * np.mean(np.abs(tp - tp.mean())))
check("CCI(20) = (typical price - its mean) / (0.015 x mean deviation)", abs(val(sc._i("cci", period=20)) - cci_ref) < 1e-9, round(cci_ref, 2))
hh, ll = hi_.iloc[-14:].max(), lo_.iloc[-14:].min()
check("Williams %R(14) = -100 x (highest high - close) / (range)", abs(val(sc._i("williams_r", period=14)) - (-100 * (hh - d.iloc[-1]) / (hh - ll))) < 1e-9)
wr = val(sc._i("williams_r", period=14))
check("Williams %R stays between -100 and 0", -100 <= wr <= 0, round(wr, 2))
upt = sc.frames(daily(100 * np.cumprod(np.full(120, 1.004))))["daily"]
ps = sc._psar(upt["High"], upt["Low"], 0.02, 0.2)
check("Parabolic SAR sits below the lows in a steady uptrend", (ps.iloc[-100:] < upt["Low"].iloc[-100:]).all())


def _ref_sar(h, l, step=0.02, mx=0.2):
    # Written separately from the engine, straight from Wilder's rules.
    out, bull, af, ep, sar = [np.nan] * len(h), True, step, h[0], l[0]
    for i in range(1, len(h)):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, l[i - 1], l[max(i - 2, 0)])
            if l[i] < sar: bull, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep: ep, af = h[i], min(af + step, mx)
        else:
            sar = max(sar, h[i - 1], h[max(i - 2, 0)])
            if h[i] > sar: bull, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep: ep, af = l[i], min(af + step, mx)
        out[i] = sar
    return np.array(out)


dh, dl = fr["daily"]["High"], fr["daily"]["Low"]
check("SAR matches Wilder's rules bar for bar on choppy prices", np.nanmax(np.abs(sc._psar(dh, dl, 0.02, 0.2).to_numpy()[-200:] - _ref_sar(dh.to_numpy(), dl.to_numpy())[-200:])) < 1e-9)
flip = daily(list(100 * np.cumprod(np.full(60, 1.004))) + list(100 * 1.004 ** 60 * np.cumprod(np.full(20, 0.985))))
fd = sc.frames(flip)["daily"]
pf = sc._psar(fd["High"], fd["Low"], 0.02, 0.2)
check("and flips above the highs once the trend turns down", (pf.iloc[-5:] > fd["High"].iloc[-5:]).all() and (pf.iloc[55:60] < fd["Low"].iloc[55:60]).all())
try:
    sc.clean({"conditions": [{"lhs": sc._i("psar", step=0.08, max_step=0.06), "op": ">", "rhs": sc._v(0)}]}); check("SAR step above its maximum is refused", False)
except ValueError as e:
    check("SAR step above its maximum is refused", True, str(e))
check("SAR settings keep their decimals", sc.clean({"conditions": [{"lhs": sc._i("close"), "op": ">", "rhs": sc._i("psar", step=0.025, max_step=0.25)}]})["conditions"][0]["rhs"]["params"] == {"step": 0.025, "max_step": 0.25})

print("2. CROSSES")
up = daily(list(np.linspace(100, 90, 60)) + [96])      # falling, then jumps back over its short average
s = {"name": "x", "conditions": [{"lhs": sc._i("close"), "op": "crosses above", "rhs": sc._i("sma", period=5)}]}
check("crosses above: only on the bar it crosses", sc.run({"A": up}, s)["matches"] and not sc.run({"A": daily(list(np.linspace(100, 90, 60)) + [96, 97])}, s)["matches"])

print("3. SCREENS THAT MUST BE REFUSED")
def refused(label, strat):
    try:
        sc.clean(strat); check(label, False, "accepted")
    except ValueError as e:
        check(label, True, str(e))
refused("an unknown indicator", {"conditions": [{"lhs": sc._i("magic"), "op": ">", "rhs": sc._v(1)}]})
refused("a number on the left", {"conditions": [{"lhs": sc._v(1), "op": ">", "rhs": sc._v(1)}]})
refused("an unknown comparison", {"conditions": [{"lhs": sc._i("close"), "op": "==", "rhs": sc._v(1)}]})
refused("a period out of range", {"conditions": [{"lhs": sc._i("sma", period=5000), "op": ">", "rhs": sc._v(1)}]})
refused("MACD fast not shorter than slow", {"conditions": [{"lhs": sc._i("macd", fast=30, slow=26, signal=9), "op": ">", "rhs": sc._v(0)}]})
refused("a bad timeframe", {"conditions": [{"lhs": sc._i("close", tf="hourly"), "op": ">", "rhs": sc._v(1)}]})
refused("too many conditions", {"conditions": [{"lhs": sc._i("close"), "op": ">", "rhs": sc._v(1)}] * 9})
refused("no conditions", {"conditions": []})
refused("code smuggled in as a name", {"conditions": [{"lhs": {"type": "indicator", "name": "__import__('os')"}, "op": ">", "rhs": sc._v(1)}]})

print("4. THE SOURCE PROJECT'S STRATEGY FILES LOAD AS THEY ARE")
theirs = {"name": "Momentum Gain", "conditions": [
    {"lhs": {"type": "indicator", "name": "rsi", "params": {"period": 14}, "timeframe": "daily", "offset": 0}, "operator": ">", "rhs": {"type": "value", "value": 60}},
    {"lhs": {"type": "indicator", "name": "close", "params": {}, "timeframe": "daily", "offset": 0}, "operator": ">",
     "rhs": {"type": "indicator", "name": "ema", "params": {"period": 50}, "timeframe": "daily", "offset": 0, "multiplier": 1.0, "add_offset": 0.0}},
    {"lhs": {"type": "indicator", "name": "macd", "params": {"period_fast": 12, "period_slow": 26, "period_signal": 9}, "timeframe": "weekly", "offset": 0}, "operator": ">", "rhs": {"type": "value", "value": 0}}]}
c = sc.clean(theirs)
check("their field names map across", c["conditions"][0]["op"] == ">" and c["conditions"][1]["rhs"]["name"] == "ema" and c["conditions"][2]["lhs"]["params"] == {"fast": 12, "slow": 26, "signal": 9} and c["conditions"][2]["lhs"]["tf"] == "weekly", c["conditions"][2]["lhs"])

print("5. RUNNING: MATCHES, AND HONEST GAPS")
# Real prices have down days. A series with none leaves RSI undefined, and
# TradePicker's own rsi() reads that as a neutral 50, so the fixture is a noisy
# uptrend - and its properties are checked before the screen is asked about it.
g = np.random.default_rng(11)
strong = daily(100 * np.cumprod(1 + g.normal(0.006, 0.006, 280)), np.r_[np.full(279, 1e6), 3e6])
weak = daily(100 * np.cumprod(1 + g.normal(-0.004, 0.006, 280)))
fs = sc.frames(strong)["daily"]
check("fixture really is strong: RSI > 60, above EMA(50), volume jump",
      ind.rsi(fs["Close"], 14).iloc[-1] > 60 and fs["Close"].iloc[-1] > ind.ema(fs["Close"], 50).iloc[-1]
      and fs["Volume"].iloc[-1] > fs["Volume"].iloc[-21:-1].mean(), round(float(ind.rsi(fs["Close"], 14).iloc[-1]), 1))
short = daily(np.linspace(100, 120, 40))
res = sc.run({"STRONG": strong, "WEAK": weak, "SHORT": short}, sc.PRESETS[0], sc.members())
check("momentum preset picks the rising stock with a volume jump, not the falling one", [m["sym"] for m in res["matches"]] == ["STRONG"], [m["sym"] for m in res["matches"]])
check("a stock without 50 bars for the EMA is listed as not enough history, not matched", "SHORT" in res["not_enough_history"])
check("each match carries the values that decided it", len(res["matches"][0]["values"]) == 3 and all(v["ok"] for v in res["matches"][0]["values"]))
check("index and sector come from the member lists", sc.members()["HDFCBANK"]["indices"] and sc.members()["HDFCBANK"]["sector"])
for p in sc.PRESETS:
    try:
        sc.run({"STRONG": strong, "WEAK": weak}, p); ok = True
    except Exception as e:
        ok = str(e)
    check(f"preset runs: {p['name']}", ok is True, ok)

print("6. SAVED SCREENS")
EM = "screener-test@example.invalid"
sc.save(EM, dict(sc.PRESETS[1], name="My oversold"))
sc.save(EM, dict(sc.PRESETS[0], name="my OVERSOLD"))
check("saving under the same name replaces it", len(sc.saved(EM)) == 1 and sc.saved(EM)[0]["conditions"] == sc.PRESETS[0]["conditions"])
check("kept in the account's private folder", os.sep + "users" + os.sep in sc._saved_path(EM))
sc.delete(EM, "My Oversold")
check("deleting removes it", sc.saved(EM) == [])
for k in range(sc.MAX_SAVED):
    sc.save(EM, dict(sc.PRESETS[0], name=f"s{k}"))
try:
    sc.save(EM, dict(sc.PRESETS[0], name="one too many")); check("a limit on saved screens", False)
except ValueError:
    check("a limit on saved screens", True)

print()
print("SCREENER TEST PASSED" if not fails else f"SCREENER TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
