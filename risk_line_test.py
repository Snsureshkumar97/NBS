#!/usr/bin/env python3
"""The Signal page's cost-and-risk line, run for real in node - with the capital box and the daily loss limit gone.

The user (25 Sep 2026), looking at the Signal page: "can we remove that capital and daily loss limit and the session
part also". The box held a Capital field, a Risk-per-trade choice and one paragraph: what the trade costs, what it
risks, "= 1.20% of capital", "At 2% risk the account carries 1 lot", the daily loss limit, then the spread and the
expiry-day warning. The two inputs and everything that reads the capital are gone; the money facts, the spread and the
warning stay - they are about the trade, not the account. And an empty box is not left on the page for "No trade".

The daily loss limit itself is NOT removed from the server (tickets.py still stops new tickets once closed losses pass it):
only its place on this screen. That is pinned here too, so removing the box is never taken for removing the brake.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
SRC = open(os.path.join(HERE, "web_server.py")).read()

print("1. THE PAGE")
check("no Capital field, no Risk-per-trade choice, no handler for either",
      'id="capital"' not in SRC and 'id="riskpct"' not in SRC and '$("capital")' not in SRC and '$("riskpct")' not in SRC
      and "function postRisk" not in SRC and "CAPFOCUS" not in SRC)
check("the box that is left holds only the line", '<div class="risk" id="risk">\n  <div class="riskline" id="riskline"></div>\n  </div>' in SRC)
check("the command palette no longer offers to set capital", "Set capital and risk per trade" not in SRC)
body = SRC[SRC.index("function riskBox("):SRC.index("// ============================================================== journal")]
check("riskBox reads no capital, no risk percentage, no loss limit",
      not re.search(r"\bcap\b|risk_pct|loss_limit|of capital|the account carries|Daily loss limit", body), "")

print("2. THE SERVER KEEPS THE BRAKE")
tk = open(os.path.join(HERE, "tickets.py")).read()
check("tickets.py still refuses new tickets once today's closed losses pass the limit",
      'return ("loss_limit", "LOSS LIMIT"' in tk and "booked <= -limit" in tk)
check("and its message no longer points at a capital box that is not there", "capital you entered" not in tk)

if not NODE:
    check("node is available to run the page's own function", False)
else:
    a = SRC.index("const num=")
    b = SRC.index("function unitLabel(")
    h = SRC.index("let CCY = ")
    i = SRC.index("function feedTag(")
    j = SRC.index("function riskBox(")
    k = SRC.index("// ============================================================== journal")
    prog = """
const assert = require("assert");
const EL = {};
function mkEl(id){ return {id, innerHTML: "", textContent: "", value: "", style: {}, dataset: {}}; }
function $(id){ return EL[id] || (EL[id] = mkEl(id)); }
let LAST = null, CUR = "NIFTY";
""" + SRC[h:i] + SRC[a:b] + SRC[j:k] + r'''
const box = () => $("risk").style.display, txt = () => $("riskline").innerHTML;

// a signal with a live premium and a stop: cost and risk for the lots picked, nothing about capital
const sig = {ltp: 130.0, premium_stop: 110.0, lot_size: 65, bias: "BULLISH", charges: null};
riskBox(sig, null, {capital: 200000, risk_pct: 2, loss_limit: 12000, loss_limit_pct: 6, booked: 0});
assert.ok(txt().includes("Cost of this signal") && txt().includes("Risk on this signal"), "the money facts stay - " + txt());
assert.ok(!/capital|Daily loss limit|account carries/.test(txt()), "...but say nothing of capital, the loss limit or sizing to the account - " + txt());
assert.strictEqual(box(), "", "the box is shown when it has something to say");

// the same with no session data at all (a fresh account): the same line, the same words
riskBox(sig, null, undefined);
assert.ok(txt().includes("Risk on this signal") && !/capital/.test(txt()), "no session needed - " + txt());

// an open ticket, 2 lots
const tk = {open: true, tracked_on: "premium", entry: 130.0, stop: 110.0, lot_size: 65, lots: 2, charges: null};
riskBox({lot_size: 65}, tk, {});
assert.ok(txt().includes("Cost of this ticket") && txt().includes("for 2 lots"), "an open ticket is costed for its lots - " + txt());
assert.ok(txt().includes("16,900") && txt().includes("8,450 per lot"), "...the cost is for BOTH lots (130 x 65 x 2), the per-lot figure beside it - " + txt());

// "No trade": nothing to say, and no empty box
riskBox({bias: "NEUTRAL"}, null, {capital: 200000});
assert.strictEqual(txt(), "", "nothing to say on No trade");
assert.strictEqual(box(), "none", "...and the box is hidden, not left empty");

// a signal with no premium stop yet still says why the money cannot be worked out
riskBox({ltp: 130.0, bias: "BEARISH", lot_size: 65}, null, {});
assert.ok(txt().includes("cannot be worked out yet"), "no stop yet, and it says so - " + txt());

// the spread and the expiry-day warning are about the trade and stay
riskBox(Object.assign({}, sig, {spread: {pct: 0.4, bid: 129.5, ask: 130.5}, max_spread: 2, expiry_today: true}), null, {});
assert.ok(txt().includes("Spread") && txt().includes("Expires today"), "the spread and the expiry-day warning stay - " + txt());
console.log("ok:riskline");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    print(r.stdout.strip()[-400:], r.stderr.strip()[-900:])
    check("riskBox behaves as above, run in node", r.returncode == 0 and "ok:riskline" in r.stdout)

print("RISK LINE TEST PASSED" if not fails else f"RISK LINE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
