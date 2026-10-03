#!/usr/bin/env python3
"""The Signal page's target ladder on an Exness ticket (4 Oct 2026, the user: "beside the targets
it dont show the amount for t1" / "targets and stop loss", and no lots selector on Bitcoin).
Runs the page's OWN ladder() in Node against a fake DOM - skipped (not failed) without Node."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


NODE = shutil.which("node")
if not NODE:
    print("EXNESS LADDER TEST SKIPPED - no node on this machine")
    sys.exit(0)
scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", web_server.PAGE, re.S)
main = next(s for s in scripts if "function ladder(r, tk)" in s)
HARNESS = r'''
const els = {};
function mkEl(id){
  return {id, style: {}, dataset: {}, innerHTML: "", textContent: "", value: "", hidden: false, disabled: false,
    classList: {toggle(){}, add(){}, remove(){}, contains(){ return false; }}, setAttribute(){}, getAttribute(){ return null; },
    addEventListener(){}, removeEventListener(){}, appendChild(){}, querySelector(){ return mkEl("q"); },
    querySelectorAll(){ return []; }, closest(){ return null; }, after(){}, focus(){}, remove(){}, getContext(){ return null; },
    add(o){ (this.options = this.options || []).push(o); },
    getBoundingClientRect(){ return {width: 100, height: 100, top: 0, left: 0}; }};
}
globalThis.document = {getElementById: id => els[id] || (els[id] = mkEl(id)), querySelector: () => mkEl("q"),
  querySelectorAll: () => [], addEventListener(){}, createElement: () => mkEl("new"), body: mkEl("body"),
  documentElement: mkEl("html"), hidden: false, cookie: ""};
globalThis.window = globalThis; globalThis.location = {search: "", hash: "", pathname: "/app", href: "/app"};
globalThis.localStorage = {getItem(){ return null; }, setItem(){}}; globalThis.sessionStorage = globalThis.localStorage;
globalThis.navigator = {userAgent: "node"}; globalThis.matchMedia = () => ({matches: false, addEventListener(){}});
globalThis.fetch = () => new Promise(() => {}); globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
globalThis.requestAnimationFrame = () => 0; globalThis.getComputedStyle = () => ({getPropertyValue: () => ""});
globalThis.Option = function(t, v){ this.text = t; this.value = v; };
globalThis.ResizeObserver = function(){ this.observe = () => {}; }; globalThis.IntersectionObserver = globalThis.ResizeObserver;
globalThis.MutationObserver = globalThis.ResizeObserver; globalThis.addEventListener = () => {};
'''
TEST = r'''
;(function(){
  const rs = () => [...document.getElementById("ladder").innerHTML.matchAll(/class="rs"[^>]*>([^<]*)</g)].map(m => m[1]);
  const out = {};
  LAST = {market: "crypto", currency: "USD", session: {lot_choices: [0.01,0.02,0.05,0.1,0.25,0.5,1], lots: 0.25},
          broker: {accounts: []}, market_open: true};
  LOTS = 0.25; LOTS_SYNCED = true;
  ladder({index: "BTC", cfd: true, option_type: "PE", bias: "BEARISH", spot: 84000, lot_size: 1, cfd_spread: 10,
          targets: [83500, 83000, 82500], stop: 84300, exit_at: "T3", premium_targets: [null,null,null]}, null);
  out.btc_sell = rs();
  out.lots_shown = document.getElementById("lotswrap").style.display;
  out.choices = (document.getElementById("lots").options || []).map(o => o.value);
  out.levels = [...document.getElementById("ladder").innerHTML.matchAll(/class="n"[^>]*>([^<]*)</g)].map(m => m[1]);
  ladder({index: "GOLD", cfd: true}, {open: true, cfd: true, index: "GOLD", option_type: "CE", tracked_on: "index",
          entry: 4140.44, now: 4141, lot_size: 100, lots: 0.1, entry_spread: 0.26,
          targets: [4150.44, 4160.44, 4170.44], stop: 4134.44, exit_at: "T3", hit: {}, hit_time: {}});
  out.gold_ticket = rs();
  LOTS = 1;
  ladder({index: "NIFTY", option_type: "CE", ltp: 100, premium_targets: [120,140,160], premium_stop: 80,
          targets: [25100,25200,25300], stop: 24900, spot: 25000, lot_size: 65, premium_source: "live", exit_at: "T2"}, null);
  out.nifty = rs();
  console.log(JSON.stringify(out));
})();
'''
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
    fh.write(HARNESS + main + TEST)
r = subprocess.run([NODE, fh.name], capture_output=True, text=True, timeout=60)
import json
try:
    out = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    print(r.stdout[-800:], r.stderr[-800:])
    out = {}
print("1. AN EXNESS SIGNAL: THE LOTS SELECTOR, AND DOLLARS BESIDE EVERY TARGET AND THE STOP")
check("the lots selector shows, with Exness's 0.01 - 1 lots", out.get("lots_shown") == "flex"
      and out.get("choices") == ["0.01", "0.02", "0.05", "0.1", "0.25", "0.5", "1"], out.get("choices"))
check("a SELL at 84,000, 0.25 lot, $10 spread: T1 83,500 = 500 x 0.25 - 2.50 = +$122.50 ... stop 84,300 = -$77.50",
      out.get("btc_sell") == ["+$122.50", "+$247.50", "+$372.50", "−$77.50"], out.get("btc_sell"))
check("prices to the cent", out.get("levels") == ["83,500.00", "83,000.00", "82,500.00", "84,300.00"], out.get("levels"))
print("2. AN OPEN EXNESS TICKET")
check("gold BUY 0.1 lot (10 oz), spread 0.26: T1 +$10/oz = +$97.40 ... stop -$62.60",
      out.get("gold_ticket") == ["+$97.40", "+$197.40", "+$297.40", "−$62.60"], out.get("gold_ticket"))
print("3. THE INDIAN PREMIUM LADDER IS UNCHANGED")
check("NIFTY 1 lot (65): T1 120 vs 100 = +1,300 ... stop -1,300 (whole rupees, as before)",
      [x[-5:] for x in out.get("nifty") or []] == ["1,300", "2,600", "3,900", "1,300"]
      and (out.get("nifty") or [""])[0].startswith("+") and (out.get("nifty") or ["", "", "", ""])[3].startswith("−"),
      out.get("nifty"))
print()
if fails:
    print(f"EXNESS LADDER TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("EXNESS LADDER TEST PASSED")
