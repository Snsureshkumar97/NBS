#!/usr/bin/env python3
"""No line on the Signal page may read as "don't enter" unless it is a check that stops one (the user, 16 Oct 2026, after a
Nifty ticket at RSI 72: "the ticket was saying when rsi was high entering a trade something but why the tool still
entered" -> "please check with the other votes also anykind of these things ... and fix it"). The RSI line past its
outer bands says RSI does not vote and that this never stops a trade, with what the 3-year test found; inside them it
says plainly which way it votes; every vote's sentence agrees with its own vote; the Market trend card says it is a
description, not one of the tool's checks. No network."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config
import explain

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

WARNINGS = ("too stretched to call", "likely pullback", "likely bounce", "snap back", "bounce is due", "don't enter",
            "do not enter", "avoid")

print("1. THE RSI LINE")
lo, mid, hi = config.RSI_OVERSOLD, config.RSI_BULL_MIN, config.RSI_OVERBOUGHT
for r, sc, want in ((71.8, 1, "a vote for a call"), (hi + 3.3, 0, "does not vote either way"),
                    (lo - 3.0, 0, "does not vote either way"), (38.0, -1, "a vote for a put"), (50.0, 0, "no vote")):
    t = explain._rsi_reason({"last_rsi": r, "rsi_score": sc})["text"]
    check(f"RSI {r:g}: says '{want}' and nothing that reads as a warning", want in t
          and not any(w in t.lower() for w in WARNINGS), t)
t = explain._rsi_reason({"last_rsi": hi + 3.3, "rsi_score": 0})["text"]
check("past the band: it never stops a trade on its own, the other votes decide, and what the test found",
      "never stops a trade on its own" in t and "the other votes decide" in t and "lost money in both test periods" in t, t)

print("2. EVERY VOTE'S SENTENCE MATCHES ITS VOTE")
tech_up = {"trend_score": 1, "last_close": 23100.0, "ema_fast": 23080.0, "ema_slow": 23050.0, "macd_score": 1,
           "macd_hist": 4.7, "rsi_score": 1, "last_rsi": 66.0, "vwap_score": 1, "vwap_gap": 84.9, "vwap": 23015.0}
tech_flat = dict(tech_up, trend_score=0, macd_score=0, macd_hist=0.0, rsi_score=0, last_rsi=50.0, vwap_score=0, vwap_gap=0.0)
for tech, words in ((tech_up, ("uptrend", "upward momentum", "a vote for a call", "average buyer is in profit")),
                    (tech_flat, ("stays out of the vote", "no vote", "no vote", "neither side owns the day"))):
    rows = [explain._trend_reason(tech), explain._macd_reason(tech), explain._rsi_reason(tech), explain._vwap_reason(tech)]
    ok = all(w in r["text"] for r, w in zip(rows, words)) and not any(any(x in r["text"].lower() for x in WARNINGS) for r in rows)
    check(f"{'all up' if tech is tech_up else 'all neutral'}: each line says its own vote, none reads as a warning", ok,
          [r["text"][:60] for r in rows])

print("3. THE MARKET TREND CARD SAYS WHAT IT IS")
src = open(os.path.join(HERE, "web_server.py")).read()
card = src[src.index('<p class="eyebrow" role="heading" aria-level="2">Market trend</p>'):src.index('aria-level="2">Day move</p>')]
check("a note under it: a description of the market, not one of the tool's checks, never stops or starts a trade",
      'class="sub trendnote"' in card and "not one of the tool's checks" in card and "never stops or starts a" in card
      and ".trendnote{" in src)

print("4. ONLY THE VOTES THE DECISION COUNTS ARE LISTED (19 Oct 2026)")
# "in crypto market if the market doest have PCR why are we still counting pcr as the vote" - a CFD has no options,
# and crypto drops RSI (config.VOTE_OVERRIDES); both rows were listed and the ring over the rows counted them.
def rows(index, oi=None):
    rec = {"index": index, "technical": tech_up, "option_chain": oi or {}, "bias": "NEUTRAL", "adx": 30.0}
    return [v["name"] for v in explain.explain(rec)["votes"]]
for name in ("BTC", "GOLD"):
    if config.is_cfd(name):
        got = rows(name)
        counted = [v for v in ("Trend", "MACD", "RSI", "VWAP") if v not in config.vote_overrides(name).get("drop", ())]
        check(f"{name} (a CFD, no options): no PCR row, no RSI row - exactly the votes its decision counts",
              got == counted and "PCR" not in got, got)
got = rows("NIFTY")
check("Nifty with its chain missing still lists PCR (n/a) - a fault worth seeing there - and RSI",
      got == ["Trend", "MACD", "RSI", "VWAP", "PCR"], got)
got = rows("NIFTY", {"available": True, "pcr": 1.31, "oi_score": 1})
check("Nifty with its chain: all five, PCR among them", got == ["Trend", "MACD", "RSI", "VWAP", "PCR"], got)
check("a recommendation with no index (older callers) keeps all five rows", rows(None) == ["Trend", "MACD", "RSI", "VWAP", "PCR"])

print()
print("SIGNAL WORDING TEST PASSED" if not fails else f"SIGNAL WORDING TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
