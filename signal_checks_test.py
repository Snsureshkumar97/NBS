#!/usr/bin/env python3
"""The checklist beside the rule signal: each check's three answers on both
sides, the crypto and Indian flow readings, the summary and the log stamp, the
feed that builds it, the trade log that keeps it, and the page that shows it.
It must never gate an entry. Fakes only - nothing trades, nothing reaches a venue."""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import datetime as dt

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
config.ENABLE_CRYPTO = True
import feeds
import signal_checks as sc
import trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def item(c, key):
    return next(i for i in c["items"] if i["key"] == key)


def rec(side="CE", spot=23400.0, risk=40.0, targets=(23460.0, 23500.0, 23560.0), wall=None, spread=0.4, index="NIFTY"):
    r = {"index": index, "option_type": side, "spot": spot, "risk_points": risk, "index_targets": list(targets),
         "spread": {"pct": spread} if spread is not None else None}
    if wall is not None:
        r["option_chain"] = {"top_call_oi_strike": wall, "top_put_oi_strike": wall}
    return r


G_UP = {"nearest_resistance": 23500.0, "nearest_support": 23300.0, "volume_oscillator": {"value_pct": 12.0}}

print("1. NO SIDE, NO CHECKLIST")
check("a wait has nothing to compare against", sc.evaluate({"option_type": None, "spot": 1}) is None and sc.evaluate(None) is None
      and sc.evaluate({"option_type": "XX"}) is None)

print("2. GANN ROOM - THE SAME TEST AS THE STUDY: NEARER THAN THE STOP IS AGAINST")
c = sc.evaluate(rec("CE"), {"nearest_resistance": 23420.0, "nearest_support": 23300.0}, None, "nse_index", "NIFTY")
check("a resistance 20 points away with a 40-point stop is against a call", item(c, "gann")["status"] == "against" and "nearer than the stop" in item(c, "gann")["detail"], item(c, "gann"))
c = sc.evaluate(rec("CE"), {"nearest_resistance": 23500.0}, None, "nse_index", "NIFTY")
check("100 points away, past the first target at 60, agrees", item(c, "gann")["status"] == "agrees")
c = sc.evaluate(rec("CE"), {"nearest_resistance": 23450.0}, None, "nse_index", "NIFTY")
check("50 points away - past the stop, short of the first target - is neutral", item(c, "gann")["status"] == "neutral")
c = sc.evaluate(rec("PE", targets=(23340.0, 23300.0, 23240.0)), {"nearest_resistance": 23500.0, "nearest_support": 23390.0}, None, "nse_index", "NIFTY")
check("a put reads the support below, not the resistance above", item(c, "gann")["status"] == "against" and "support" in item(c, "gann")["detail"])
c = sc.evaluate(rec("CE"), None, None, "nse_index", "NIFTY")
check("no Gann report, no answer", item(c, "gann")["status"] == "no_data")

print("3. VOLUME")
check("rising participation agrees, fading is against, none is no data",
      [item(sc.evaluate(rec(), {"volume_oscillator": {"value_pct": v}}, None, "nse_index", "NIFTY"), "volume")["status"] for v in (5.0, -3.0, 0.0, None)]
      == ["agrees", "against", "against", "no_data"])

print("4. BITCOIN'S TAKER FLOW - ONLY ONCE THE TAPE COVERS THE 15 MINUTES")
def tf(c15, complete=True, covers=20.0):
    return {"windows": {"15m": {"complete": complete, "cvd_pct_of_volume": c15}}, "tape_covers_minutes": covers,
            "in_words": "Over 15 minutes price rose 0.31%."}
r_btc = rec("CE", spot=85000, risk=300, targets=(85400, 85800, 86200), index="BTC")
check("takers buying agree with a call and are against a put; a lean under 10% is neutral",
      [item(sc.evaluate(r_btc, None, tf(v), "crypto", "BTC"), "flow")["status"] for v in (+25.0, -25.0, +5.0)] == ["agrees", "against", "neutral"]
      and item(sc.evaluate(dict(r_btc, option_type="PE"), None, tf(-25.0), "crypto", "BTC"), "flow")["status"] == "agrees")
c = sc.evaluate(r_btc, None, tf(+25.0, complete=False, covers=9.5), "crypto", "BTC")
check("a tape that does not cover 15 minutes gives no answer, and says how much it holds",
      item(c, "flow")["status"] == "no_data" and "9.5" in item(c, "flow")["detail"], item(c, "flow"))
check("gold keeps no tape, so it says so - not that the tape has not covered 15 minutes",
      item(sc.evaluate(rec("CE", index="GOLD"), None, None, "crypto", "GOLD"), "flow")["status"] == "no_data"
      and "only read for Bitcoin" in item(sc.evaluate(rec("CE", index="GOLD"), None, None, "crypto", "GOLD"), "flow")["detail"])
check("no taker flow at all gives no answer", item(sc.evaluate(r_btc, None, None, "crypto", "BTC"), "flow")["status"] == "no_data")
check("the crypto checklist has no heavyweights line, even if a reading carried the numbers",
      "heavyweights" not in [i["key"] for i in sc.evaluate(r_btc, None, dict(tf(1.0), heavyweights_weight_pct={"bullish_buildup": 30, "bearish_buildup": 1}), "crypto", "BTC")["items"]])

print("5. THE INDIAN FUTURES' FLOW")
def fut(buildup, lean):
    return {"index_future": {"buildup_today": buildup, "order_flow": {"book_imbalance": lean}}}
call = lambda flow: item(sc.evaluate(rec("CE"), None, flow, "nse_index", "NIFTY"), "flow")["status"]
check("long build-up and a bid-heavy book agree with a call; the opposite is against", call(fut("long build-up", 0.3)) == "agrees"
      and call(fut("short build-up", -0.3)) == "against")
check("...and the mirror image for a put", item(sc.evaluate(rec("PE"), None, fut("short build-up", -0.3), "nse_index", "NIFTY"), "flow")["status"] == "agrees")
check("build-up and book pulling opposite ways is neutral; a balanced book adds nothing", call(fut("long build-up", -0.3)) == "neutral"
      and call(fut("long build-up", 0.05)) == "agrees" and call(fut("no clear build-up", 0.0)) == "neutral")
check("one half known is still an answer; none is no data", call({"index_future": {"buildup_today": "long build-up"}}) == "agrees"
      and call({"index_future": {"order_flow": {"book_imbalance": -0.4}}}) == "against" and call({"index_future": {}}) == "no_data" and call(None) == "no_data")

print("6. THE OI WALL IN THE WAY")
w = lambda side, wall, **k: item(sc.evaluate(rec(side, wall=wall, **k), None, None, "nse_index", "NIFTY"), "walls")
check("the biggest call OI between spot and the first target is against a call", w("CE", 23450.0)["status"] == "against")
check("beyond the first target it agrees; already behind spot it agrees", w("CE", 23600.0)["status"] == "agrees" and w("CE", 23300.0)["status"] == "agrees")
check("for a put it is the put OI below: between spot and the target against, beyond it agrees",
      item(sc.evaluate(rec("PE", targets=(23340.0, 23300.0, 23240.0), wall=23360.0), None, None, "nse_index", "NIFTY"), "walls")["status"] == "against"
      and item(sc.evaluate(rec("PE", targets=(23340.0, 23300.0, 23240.0), wall=23200.0), None, None, "nse_index", "NIFTY"), "walls")["status"] == "agrees")
check("no chain is no data; a wall with no target is neutral", item(sc.evaluate(rec("CE"), None, None, "nse_index", "NIFTY"), "walls")["status"] == "no_data"
      and item(sc.evaluate(rec("CE", targets=(), wall=23450.0), None, None, "nse_index", "NIFTY"), "walls")["status"] == "neutral")

print("7. THE SPREAD, AGAINST THE INSTRUMENT'S OWN LIMIT")
sp = lambda pct, index="NIFTY": item(sc.evaluate(rec("CE", spread=pct, index=index), None, None, "nse_index", index), "spread")["status"]
check("under half the limit agrees, up to the limit is neutral, over it is against, none is no data",
      [sp(0.4), sp(2.0), sp(4.0), sp(None)] == ["agrees", "neutral", "against", "no_data"], [sp(0.4), sp(2.0), sp(4.0), sp(None)])
check("gold's own 8% limit is the yardstick for gold", sp(5.0, "GOLD") == "neutral" and sp(3.5, "GOLD") == "agrees" and sp(5.0, "NIFTY") == "against")

print("8. THE HEAVYWEIGHTS - INDIAN INDICES ONLY")
hv = lambda up, down, side="CE": [i for i in sc.evaluate(rec(side), None, {"heavyweights_weight_pct": {"bullish_buildup": up, "bearish_buildup": down}},
                                                          "nse_index", "NIFTY")["items"] if i["key"] == "heavyweights"]
check("most of the weight building long agrees with a call and is against a put", hv(30, 10)[0]["status"] == "agrees" and hv(30, 10, "PE")[0]["status"] == "against")
check("an even split is neutral; nothing classified is no data", hv(20, 18)[0]["status"] == "neutral" and hv(0, 0)[0]["status"] == "no_data")
check("no heavyweights in the reading, no line at all", hv.__call__(0, 0) and not [i for i in sc.evaluate(rec(), None, {}, "nse_index", "NIFTY")["items"] if i["key"] == "heavyweights"])

print("9. THE SUMMARY AND THE LOG STAMP")
c = sc.evaluate(rec("CE", wall=23450.0), {"nearest_resistance": 23500.0, "volume_oscillator": {"value_pct": -2.0}}, fut("long build-up", 0.3), "nse_index", "NIFTY")
check("counts add up to the checks made and the sentence says them", c["agree"] + c["against"] + c["neutral"] + c["no_data"] == len(c["items"])
      and c["summary"].startswith(f"{c['agree']} agree · {c['against']} against"), c["summary"])
check("the note says the rules do not use it", "do not use them to enter or skip" in c["note"])
a, b, text = sc.stamp(c)
check("the stamp is the agree and against counts and one word-and-symbol per check", a == c["agree"] and b == c["against"]
      and text == "gann+ volume- flow+ walls- spread+", text)
check("no checklist, no stamp", sc.stamp(None) == (None, None, None))

print("10. THE TRADE LOG KEEPS IT - AND OLD FILES STILL READ")
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
now = dt.datetime(2026, 9, 21, 10, 0, 0)
trade = {"trade_id": "NIFTY-1", "index": "NIFTY", "strike": 23400, "option_type": "CE", "use_premium": True,
         "premium_targets": [110, 120, 130], "premium_sl": 90, "index_targets": [23460, 23500, 23560], "index_sl": 23360,
         "entry_ltp": 100.0, "entry_spot": 23400.0, "lot_size": 65, "lots": 1}
trade_log.log_open(trade, {"checks": c, "technical": {}}, now, path=path)
row = trade_log._read_rows(path)[0]
check("the ticket's row carries how many agreed, how many were against, and each check", row["checks_agree"] == str(c["agree"])
      and row["checks_against"] == str(c["against"]) and row["checks"] == "gann+ volume- flow+ walls- spread+", {k: row[k] for k in ("checks_agree", "checks_against", "checks")})
trade_log.log_open(dict(trade, trade_id="NIFTY-2"), {"technical": {}}, now, path=path)
check("a ticket with no checklist leaves the three columns blank", trade_log._read_rows(path)[1]["checks"] == "" and trade_log._read_rows(path)[1]["checks_agree"] == "")
old = os.path.join(tempfile.mkdtemp(), "trades.csv")
older = [f for f in trade_log.FIELDS if f not in ("checks_agree", "checks_against", "checks")]
with open(old, "w", newline="") as fh:
    wtr = csv.DictWriter(fh, fieldnames=older); wtr.writeheader(); wtr.writerow({"trade_id": "OLD-1", "event": "OPEN", "date": "2026-09-20", "index": "NIFTY"})
trade_log.log_open(trade, {"checks": c, "technical": {}}, now, path=old)
rows = trade_log._read_rows(old)
check("a log written before these columns is upgraded, keeps its old row intact and reads the new one", len(rows) == 2 and rows[0]["trade_id"] == "OLD-1"
      and rows[0]["checks"] == "" and rows[1]["checks"] == "gann+ volume- flow+ walls- spread+", [r.get("checks") for r in rows])

print("11. THE FEED BUILDS IT WITHOUT EVER GETTING IN THE WAY")
f = feeds.Feed("t:chk", "chk@example.invalid", "nse_index")
f.flow_readings = lambda: {"NIFTY": fut("long build-up", 0.3)}
got = f._signal_checks("NIFTY", rec("CE", wall=23600.0))
check("the feed hands the checklist the futures' flow for that index and the Gann report for its spot", got and item(got, "flow")["status"] == "agrees"
      and item(got, "gann")["status"] in ("agrees", "neutral", "against"), got and got["summary"])
check("no side suggested, no checklist, and no error", f._signal_checks("NIFTY", {"option_type": None, "spot": 1}) is None)
def boom(): raise RuntimeError("socket gone")
f.flow_readings = boom
check("a failure building it is a missing checklist - never an exception into the ticket engine", f._signal_checks("NIFTY", rec("CE")) is None
      and any("signal checks" in str(x) for x in getattr(f, "faults", []) or [f.__dict__.get("faults", "signal checks")]))
fc = feeds.Feed("t:chk2", "chk2@example.invalid", "crypto")
fc.flow_readings = lambda: {"BTC": tf(+25.0)}
gotc = fc._signal_checks("BTC", r_btc)
check("on the crypto market it reads the taker flow and has no heavyweights line", gotc and item(gotc, "flow")["status"] == "agrees"
      and "heavyweights" not in [i["key"] for i in gotc["items"]])
check("the payload the page reads carries it", feeds._public(dict(rec("CE"), checks=got), "NIFTY")["checks"] == got if got else False)
SRC = open(os.path.join(HERE, "feeds.py")).read()
check("both places that hand a reading to the ticket engine build it first, and neither lets it decide anything",
      SRC.count('rec["checks"] = self._signal_checks(name, rec)') == 2
      and SRC.count("self.tickets.update(name, rec)") == 2 and "checks" not in SRC.split("def update")[0][-10:])
ai = open(os.path.join(HERE, "ai_desk.py")).read()
check("an AI ticket is not stamped with the rule signal's checklist - the desk may trade the other side",
      'r["checks"] = None' in ai)
tk = open(os.path.join(HERE, "tickets.py")).read()
se = open(os.path.join(HERE, "signal_engine.py")).read()
check("nothing in the ticket engine or the signal engine reads the checklist: it cannot gate an entry",
      'get("checks")' not in tk and '["checks"]' not in tk and "signal_checks" not in tk
      and 'get("checks")' not in se and '["checks"]' not in se and "signal_checks" not in se)

print("12. THE PAGE: ROWS IN THE WHY CARD, RUN FOR REAL IN NODE")
WS = open(os.path.join(HERE, "web_server.py")).read()
check("the Signal page adds the checklist rows to its Why card", "rows += checksRows(r.checks);" in WS and "function checksRows(c){" in WS)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False, "install node")
else:
    a = WS.index("const CHECK_GLYPH"); b = WS.index("function render(s){")
    prog = "const assert = require('assert');\nconst esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');\n" + WS[a:b] + r'''
assert.strictEqual(checksRows(null), "", "no checklist, no rows");
assert.strictEqual(checksRows({items: []}), "");
const c = {side: "PE", summary: "2 agree · 1 against", note: "Not used by the rules.",
  items: [{label: "Gann room", status: "agrees", detail: "Past the first target."},
          {label: "OI wall", status: "against", detail: "<script>x</script> in the way."},
          {label: "Volume", status: "neutral", detail: "Flat."},
          {label: "Taker flow", status: "no_data", detail: "Tape holds 9 minutes."}]};
const h = checksRows(c);
assert.ok(h.includes("2 agree · 1 against") && h.includes("for a put"), "summary and the side in words");
assert.ok(h.includes("✓") && h.includes("✕") && h.includes("·") && h.includes("–"), "a glyph for each answer");
assert.ok(h.includes("<b>agrees.</b>") && h.includes("<b>against.</b>") && h.includes("<b>neutral.</b>") && h.includes("<b>no data.</b>"), "and the word, so colour is not the only carrier");
assert.ok(!h.includes("<script>") && h.includes("&lt;script&gt;"), "a detail is escaped, never run");
assert.ok(h.includes("Not used by the rules."), "the note under the rows");
assert.ok(checksRows({side: "CE", items: [{label: "x", status: "weird", detail: "d"}]}).includes("no data"), "an unknown status degrades to no data");
console.log("ok");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("rows for each answer, in words and glyphs, escaped, with the summary and the note", r.returncode == 0 and r.stdout.strip() == "ok", (r.stderr or r.stdout)[-400:])

print("SIGNAL CHECKS TEST PASSED" if not fails else f"SIGNAL CHECKS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
