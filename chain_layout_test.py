#!/usr/bin/env python3
"""The Option chain tab's layout (the user, 7 Oct 2026: "check the option chain tab layout as well"), the page's own
chainDraw() run in node: the key figures as a row over the table (the price called "Index price" / "Price", never
"Spot" - the user, 25 Sep 2026), a call below the price and a put above it tinted in the money, open interest drawn as a
bar against the biggest on screen; the option clock gone where there is no recorder, its note said once; a taller box."""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "web_server.py")).read()

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. THE SOURCE")
check("the server tells a market with no recorder so, and the page hides the clock for it",
      '"rows": [], "days": [], "unsupported": True,' in SRC and 'if(card) card.hidden = !!(d && d.unsupported);' in SRC)
check("...and the clock's note is said once, as its heading",
      'box.innerHTML = d && d.note ? "" : `<p style="color:var(--ink-3);font-size:13px;margin:0">No open-interest change in this window.</p>`;' in SRC)
check("the figures row sits over the table", SRC.index('id="chainkpi"') < SRC.index('<div class="chainwrap"><table class="chain" id="chain"></table></div>'))
check("a taller chain on a desktop", "@media (min-width:901px){.chainwrap{max-height:max(420px,calc(100vh - 340px))}}" in SRC)

print("2. chainDraw() IN NODE")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
else:
    a = SRC.index("function chainDraw(d){")
    z = SRC.index("\n}\n", a) + 3
    lines = SRC.splitlines()
    pick = lambda start: next(l for l in lines if l.startswith(start))
    prog = "\n".join([pick("const esc=s=>"), pick("const num=(v,d=2)=>"), lines[lines.index(pick("const num=(v,d=2)=>")) + 1],
                      pick("const oiFmt = v =>"), lines[lines.index(pick("const oiFmt = v =>")) + 1],
                      pick("const wlId = ")]) + r'''
const assert = require("assert");
let CHAIN_SCROLLED = "X";
const WL = {ids: new Set()}, WSTAR = "*";
class El { constructor(){ this.innerHTML = ""; this.textContent = ""; this.style = {setProperty(){}}; }
  querySelector(){ return null; } closest(){ return null; } }
const ELS = {chain: new El(), chainbar: new El(), chainhead: new El(), chainkpi: new El()};
const $ = id => ELS[id];
''' + SRC[a:z] + r'''
const row = (k, ceOi, peOi) => ({strike: k, ce: {ltp: 10, bid: 9, ask: 11, spread: 1, oi: ceOi}, pe: {ltp: 12, bid: 11, ask: 13, spread: 1, oi: peOi}});
chainDraw({index: "NIFTY", expiry: "2026-10-09", spot: 25010, atm: 25000, currency: "INR", pcr: 1.234, pcr_live: true,
           max_pain: 25000, call_wall: 25200, put_wall: 24800, rows: [row(24900, 50, 400), row(25000, 100, 100), row(25100, 200, 50)]});
const t = ELS.chain.innerHTML, k = ELS.chainkpi.innerHTML;
const tr = t.split("<tbody>")[1].split("</tr>");
assert.ok(tr[0].split('<td class="k">')[0].includes("itm") && !tr[0].split('<td class="k">')[1].includes("itm"),
          "24,900 (below the price): the call in the money, the put not - " + tr[0]);
assert.ok(!tr[2].split('<td class="k">')[0].includes("itm") && tr[2].split('<td class="k">')[1].includes("itm"),
          "25,100 (above the price): the put in the money, the call not");
assert.ok(tr[0].includes('<span class="oib pe" style="width:100%">') && tr[0].includes('<span class="oib ce" style="width:13%">'),
          "OI bars against the biggest on screen (400): 400 -> 100%, 50 -> 13%");
assert.ok(k.includes("<span>Index price</span><b>25,010.00</b>") && !k.includes("Spot") && k.includes("<span>PCR</span><b>1.23</b>")
          && k.includes("live, around the money") && k.includes("<span>Call wall</span>") && k.includes("<span>Put wall</span>"), k);
assert.ok(!ELS.chainbar.innerHTML.includes("Max pain"), "the figures are not repeated in the line under the table");
chainDraw({index: "BTC", expiry: "2026-10-09", spot: 83000, atm: 83000, currency: "USD", rows: [row(83000, 0, 0)]});
assert.ok(ELS.chainkpi.innerHTML.includes("<span>Price</span>") && ELS.chain.innerHTML.includes('style="width:0%"'), "crypto: Price; no OI, no bar");
chainDraw({rows: []});
assert.strictEqual(ELS.chainkpi.innerHTML, "", "no chain, no figures");
console.log("ok:chain");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("the figures (Index price, never Spot), in-the-money tint on the right side, OI bars to scale, nothing repeated",
          "ok:chain" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-900:])

print()
print("CHAIN LAYOUT TEST PASSED" if not fails else f"CHAIN LAYOUT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
