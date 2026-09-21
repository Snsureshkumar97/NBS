#!/usr/bin/env python3
"""The AI decision log: the desk's own numbering (each index numbers its own
decisions within the day) and the page that shows them grouped by day. Asked
for by the user on 21 Sep 2026 - "sort it by the dates and give numbers beside
the decision". Fakes only; nothing here decides, trades or reaches a venue."""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ai_desk

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


CLOCK = {"t": dt.datetime(2026, 9, 21, 9, 30, 0)}
tmp = tempfile.mkdtemp()


def desk():
    d = ai_desk.AIDesk.__new__(ai_desk.AIDesk)
    d.lock = threading.RLock()
    d.recent, d.decision_no, d.day = [], {}, None
    d.decisions_path = os.path.join(tmp, "ai_decisions.jsonl")
    d.state_path = os.path.join(tmp, "ai_desk.json")
    d.now = lambda: CLOCK["t"]
    d.enabled, d.decisions_today, d.decisions_by_index = {"NIFTY": True}, 0, {}
    d.tokens_today = {"input": 0, "output": 0}
    d.entries, d.contracts, d.last_candle, d.last_exit = {}, [], {}, {}
    d._lots = None
    return d


print("1. EACH INDEX NUMBERS ITS OWN DECISIONS WITHIN THE DAY")
d = desk()
ns = [d._record("NIFTY", "entry", "wait", "quiet")["n"] for _ in range(3)]
check("they count up from the day's first: #1, #2, #3", ns == [1, 2, 3], ns)
check("a second index counts on its own, not on the first's", d._record("BANKNIFTY", "entry", "wait", "quiet")["n"] == 1)
check("...and the first index carries on from where it was", d._record("NIFTY", "review", "hold", "still ok")["n"] == 4)
check("the newest decision is first in the list the page reads, and carries its number",
      d.recent[0]["index"] == "NIFTY" and d.recent[0]["n"] == 4 and d.recent[-1]["n"] == 1)
line = json.loads(open(d.decisions_path).read().strip().split("\n")[0])
check("the number is written to the permanent log too, beside the time and the reason",
      line["n"] == 1 and line["at"] == "2026-09-21 09:30:00" and line["index"] == "NIFTY")

print("2. A NEW DAY STARTS AT #1 AGAIN")
CLOCK["t"] = dt.datetime(2026, 9, 22, 9, 20, 0)
check("the first decision of the next day is #1, whether or not the day roll ran",
      d._record("NIFTY", "entry", "wait", "new day")["n"] == 1)
check("...and yesterday's entries keep the numbers they were given",
      [r["n"] for r in d.recent if r["at"][:10] == "2026-09-21" and r["index"] == "NIFTY"] == [4, 3, 2, 1])
d2 = desk()
d2.day = "2026-09-21"
d2.decision_no = {"day": "2026-09-21", "NIFTY": 7}
d2._roll_day()
check("the day roll clears the numbering", d2.decision_no == {} and d2._record("NIFTY", "entry", "wait", "x")["n"] == 1)

print("3. THE NUMBERING SURVIVES A RESTART")
d3 = desk()
d3.decision_no = {"day": "2026-09-22", "NIFTY": 5}
d3.recent = [{"at": "2026-09-22 09:31:00", "index": "NIFTY", "n": 5}]
d3._save()
saved = json.load(open(d3.state_path))
check("the count is kept on disk with the rest of the desk's state", saved["decision_no"] == {"day": "2026-09-22", "NIFTY": 5})
d4 = ai_desk.AIDesk.__new__(ai_desk.AIDesk)
d4.decision_no, d4.recent = {}, []
d4.feed = type("F", (), {"instruments": lambda self: ["NIFTY"]})()
d4.state_path = d3.state_path
d4.tokens_today = {"input": 0, "output": 0}
d4._load()
d4.lock, d4.now = threading.RLock(), lambda: dt.datetime(2026, 9, 22, 9, 45, 0)
d4.decisions_path = d3.decisions_path
check("after a restart the next decision that day is #6, not #1", d4._record("NIFTY", "entry", "wait", "back up")["n"] == 6)

print("4. THE PAGE: GROUPED BY DAY, NUMBERED, FILTERED - RUN FOR REAL IN NODE")
SRC = open(os.path.join(HERE, "web_server.py")).read()
check("the decisions list, its filter bar and their styles are on the page",
      'id="decbar"' in SRC and 'id="aidecisions"' in SRC and ".aidec .day{" in SRC and ".aidec .d .dn{" in SRC
      and "aiDecisions(d, k);" in SRC)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own functions", False, "install node to run this section")
else:
    a = SRC.index("// The decision log, grouped by day")
    b = SRC.index("function aiRender(d){")
    prog = """
const assert = require("assert");
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const EL = {};
function $(id){ return EL[id] || (EL[id] = {id, innerHTML: ""}); }
const AI = {};
""" + SRC[a:b] + r'''
const day1 = "2026-09-21", day2 = "2026-09-22";
const rows = [
  {at: day2 + " 09:45:00", index: "NIFTY", kind: "entry", action: "enter", n: 2, reason: "Trend is with it.",
   contract: "NIFTY|23400|CE|2026-09-22", entry: 91.5, target: 120, stop: 70, looked_at: ["Option chain", "Gann levels"]},
  {at: day2 + " 09:30:00", index: "NIFTY", kind: "entry", action: "wait", n: 1, reason: "ADX too low."},
  {at: day2 + " 09:29:00", index: "BTC", kind: "entry", action: "wait", n: 1, reason: "other market"},
  {at: day1 + " 14:00:00", index: "NIFTY", kind: "review", action: "exit", n: 3, reason: "Momentum gone."},
  {at: day1 + " 11:00:00", index: "NIFTY", kind: "entry", action: "rejected", reason: "Wanted a call.",
   proposal: {option_type: "CE", strike: 23500, target: 140, stop: 60}, rejected_because: "the spread is 4.1%, over the 3% limit"},
  {at: day1 + " 10:00:00", index: "NIFTY", kind: "entry", action: "wait", reason: "Chop."},
];
const data = {recent: rows};

aiDecisions(data, "NIFTY");
const html = $("aidecisions").innerHTML, bar = $("decbar").innerHTML;
assert.ok(!html.includes("other market"), "another index's decisions are not shown");
const days = html.split('<div class="day">').slice(1);
assert.strictEqual(days.length, 2, "one block per day");
const hasDate = (h, n) => /Sep/.test(h) && h.includes(String(n)) && /day/i.test(h);   // the weekday and date, in the reader's own locale order
assert.ok(hasDate(days[0].slice(0, 90), 22) && hasDate(days[1].slice(0, 90), 21), "newest day first: " + days[0].slice(0, 80));
assert.ok(days[0].includes("2 decisions") && days[0].includes("1 entry"), "the day's own tally");
assert.ok(days[1].includes("3 decisions") && days[1].includes("1 exit"));
assert.deepStrictEqual((html.match(/class="dn">#(\d+)</g) || []).map(s => s.slice(-2, -1)),
                       ["2", "1", "3", "2", "1"], "each day numbered from its own first, newest first");
assert.ok(html.includes(">Entry <b>91.5<") && html.includes(">Target <b>120<") && html.includes(">Stop <b>70<"),
          "an entry shows what it paid and where it was going");
assert.ok(html.includes("23400 CE") && html.includes("09:45") && !html.includes(day2 + " 09:45"),
          "the contract and the time of day, without repeating the date on every row");
assert.ok(html.includes("Not taken: the spread is 4.1%"), "a rejected proposal says why the tool refused it");
assert.ok(html.includes(">Wanted <b>23500 CE<"), "...and what it had wanted");
assert.ok(html.includes("Looked at Option chain, Gann levels"));
assert.ok(html.includes('class="d enter"') && html.includes('class="d exit"') && html.includes('class="d rejected"'));

// An entry from before the desk numbered them is numbered by counting up its day.
assert.ok(days[1].includes('#1') && days[1].includes('#2'), "unnumbered older rows still get a number");

assert.ok(bar.includes("All · 5") && bar.includes("Trades &amp; rejects · 3") && bar.includes("Waits &amp; holds · 2"),
          "the filter bar counts each kind: " + bar);
DEC.filter = "trades";
aiDecisions(data, "NIFTY");
const h2 = $("aidecisions").innerHTML;
assert.ok(h2.includes("23400 CE") && h2.includes("Momentum gone") && h2.includes("Not taken:") && !h2.includes("ADX too low"),
          "what it did with money: entries, exits and the proposals the tool refused");
assert.ok(h2.split('<div class="day">').length - 1 === 2, "still grouped by day when filtered");
DEC.filter = "waits";
aiDecisions(data, "NIFTY");
assert.ok($("aidecisions").innerHTML.includes("ADX too low") && !$("aidecisions").innerHTML.includes("Momentum gone"));
DEC.filter = "all";
aiDecisions(data, "SENSEX");
assert.ok($("aidecisions").innerHTML.includes("No decisions on SENSEX yet"), "an index with none says so");
assert.strictEqual($("decbar").innerHTML, "", "...and no filter bar over an empty list");
aiDecisions({recent: [{at: day2 + " 09:30:00", index: "NIFTY", action: "enter", reason: "<b>hi</b>",
                       contract: "NIFTY|1|CE|x"}]}, "NIFTY");
assert.ok($("aidecisions").innerHTML.includes("&lt;b&gt;hi&lt;/b&gt;"), "a reason is escaped, never run as markup");
console.log("ok");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("the page groups by day, numbers each day from its first, and filters without losing the grouping",
          r.returncode == 0 and r.stdout.strip() == "ok", (r.stderr or r.stdout)[-400:])

print("AI DECISIONS TEST PASSED" if not fails else f"AI DECISIONS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
