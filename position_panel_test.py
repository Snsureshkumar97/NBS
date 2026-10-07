#!/usr/bin/env python3
"""An open ticket's figures as a position panel, and money that never reads "-$0" (the user, 7 Oct 2026: "can you work
on this layout reward risk entry ... they are all boxes it should look good", after "check the whole tool for messy
numbers"). Runs the page's own posPanel() and money() in node: entry, now and the result as the headline, the rest as
label / value rows, everything escaped; a zero with no sign, dollars under 10 to the cent, rupees whole. And the Signal
card's ticket: an Exness one shows its size, value, margin and spread, and drops the price row when it only repeats Now;
an index option shows its size in qty, its cost and the index price."""
import os
import re
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

def fn(name):
    a = SRC.index(f"function {name}(")
    return SRC[a:SRC.index("\n}\n", a) + 3]

print("1. THE SOURCE")
check("the Signal card's open ticket is drawn by posPanel, not a row of boxes",
      "st.innerHTML = posPanel([[entryLabel(tk), num(tk.entry, dp)]" in SRC and 'class="tstat"' not in SRC)
check("...and so is the AI desk's ticket", "+ posPanel([[entryLabel(t), num(t.entry, dp)]" in SRC)
check("no text under 12px in the panel's own styles",
      not [x for x in re.findall(r"\.(?:psum|pdet)[^{]*\{[^}]*font-size:(\d+)px", SRC) if int(x) < 12])

print("2. THE PAGE'S OWN FUNCTIONS, IN NODE")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False, "install node to run this section")
else:
    lines = SRC.splitlines()
    pick = lambda start: next(l for l in lines if l.startswith(start))
    prog = "\n".join([pick("const esc=s=>"), pick("const num=(v,d=2)=>"), lines[lines.index(pick("const num=(v,d=2)=>")) + 1],
                      'let CCY = "USD";', fn("ccySym"), fn("ccyLocale"), fn("money"), fn("posPanel")]) + r'''
const assert = require("assert");
// money: a zero carries no sign, dollars under 10 go to the cent, rupees stay whole
assert.strictEqual(money(0), "$0");
assert.strictEqual(money(-0.004), "$0", "under half a cent is nothing");
assert.strictEqual(money(-0.28), "−$0.28");
assert.strictEqual(money(4.5), "+$4.50");
assert.strictEqual(money(58243.2), "+$58,243");
assert.strictEqual(money(-120.76), "−$121");
assert.strictEqual(money(3.2, false), "$3.20", "unsigned stays unsigned");
CCY = "INR";
assert.strictEqual(money(0), "₹0");
assert.strictEqual(money(-0.4), "₹0", "rupees: a fraction is nothing");
assert.strictEqual(money(-8121.75), "−₹8,122");
assert.strictEqual(money(4.5), "+₹5", "rupees are never to the paisa");
// the panel: headline cells then rows, every label and value escaped
const h = posPanel([["Entry", "83,083.72"], ["Now", "<b>x"], ["P&L", "−$0.28", "var(--down)"]], [["Reward : risk", "6.09 : 1"]]);
assert.ok(h.startsWith('<div class="psum"><div><span>Entry</span><b>83,083.72</b></div>'), h);
assert.ok(h.includes("&lt;b&gt;x") && !h.includes("<b><b>"), "values escaped: " + h);
assert.ok(h.includes('<span>P&amp;L</span><b style="color:var(--down)">−$0.28</b>'), h);
assert.ok(h.includes('<div class="pdet"><div><span>Reward : risk</span><b>6.09 : 1</b></div></div>'), h);
console.log("ok:panel");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("money: no -$0 / +$0, dollars to the cent under 10, rupees whole; the panel escapes everything",
          "ok:panel" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-800:])

print("3. THE SIGNAL CARD'S TICKET, DRAWN")
if NODE:
    a = SRC.index("  // The position panel. Entry and Now are the two numbers a held position is")
    z = SRC.index('  } else st.style.display = "none";', a)
    body = SRC[a:z]
    lines = SRC.splitlines()
    pick = lambda start: next(l for l in lines if l.startswith(start))
    prog = "\n".join([pick("const esc=s=>"), pick("const num=(v,d=2)=>"), lines[lines.index(pick("const num=(v,d=2)=>")) + 1],
                      'let CCY = "USD";', fn("ccySym"), fn("ccyLocale"), fn("money"), fn("posPanel")]) + r'''
const assert = require("assert");
const entryLabel = t => t.entry_real ? "Entry · filled" : "Entry";
const ST = {style: {}, innerHTML: ""};
const $ = id => ST;
function draw(tk, r, LAST){
  const open = true;
''' + body + r'''
  }
  return ST.innerHTML;
}
let h = draw({cfd: true, index: "BTC", entry: 83083.72, now: 83080.92, pnl: -0.28, lots: 0.1, lot_size: 1, entry_real: true,
              entry_spread: 6.4, reward_risk: 6.09}, {spot: 83080.92}, {broker: {accounts: [{ok: true, leverage: 200}]}});
assert.ok(h.includes("<span>Entry · filled</span><b>83,083.72</b>") && h.includes("<span>Now</span><b>83,080.92</b>"), h);
assert.ok(h.includes("<span>P&amp;L · 0.1 lot</span>") && h.includes("−$0.28"), "the result, to the cent: " + h);
assert.ok(h.includes("<span>Size</span><b>0.1 lot = 0.10 BTC · Exness</b>") && h.includes("<span>Margin (1:200)</span><b>$41.54</b>")
          && h.includes("<span>Spread paid</span><b>$0.64</b>") && h.includes("<span>Value</span><b>$8,308.37</b>"), h);
assert.ok(!h.includes("<span>Price</span>"), "the price row dropped when it only repeats Now");
h = draw({cfd: true, index: "GOLD", entry: 4087.5, now: 4090, pnl: -2.5, lots: 0.1, lot_size: 100, entry_spread: 0.2},
         {spot: 4091.25, reach_to_risk: 1.4}, {broker: {accounts: []}});
assert.ok(h.includes("<span>Price</span><b>4,091.25</b>") && h.includes("10.00 oz") && h.includes("<span>Margin</span><b>—</b>")
          && h.includes("<span>Reward : risk</span><b>1.4 : 1</b>"), "a price that differs is shown; no leverage, no margin: " + h);
CCY = "INR";
h = draw({index: "NIFTY", tracked_on: "premium", entry: 118.4, now: 131.2, pnl: 1664, lots: 2, lot_size: 65,
          strike_day_low: 96.1, strike_day_high: 140.25, reward_risk: 2.75}, {spot: 25210.4}, {});
assert.ok(h.includes("<span>P&amp;L · 2 lots</span><b") && h.includes("+₹1,664"), h);
assert.ok(h.includes("<span>Size</span><b>2 lots · 130 qty</b>") && h.includes("<span>Cost</span><b>₹15,392</b>")
          && h.includes("<span>Today's range</span><b>96.10 – 140.25</b>") && h.includes("<span>Index price</span><b>25,210</b>"), h);
console.log("ok:ticket");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("an Exness ticket: size, value, margin, spread, the result to the cent, no repeated price; an index option: "
          "size in qty, cost, the strike's range, the index price",
          "ok:ticket" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-900:])

print()
print("POSITION PANEL TEST PASSED" if not fails else f"POSITION PANEL TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
