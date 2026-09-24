#!/usr/bin/env python3
"""What the Bitcoin screen shows of Delta: the wallet in rupees, and the Greeks tab.

Reported by the user on 24 Sep 2026: "my Delta Exchange wallet is in Indian
currency but in the tool it's dollars" (the trades being in dollars is right -
Delta quotes and settles its contracts in dollars). Delta Exchange India keeps
every balance in rupees and converts at ONE fixed rate, "fixed at 85"
(guides.delta.exchange, USD-INR Rate): its API and contracts speak dollars, its
own wallet screen speaks rupees. The tool now shows the two together, so the
figure in the header is the one on Delta's own screen.

While checking which sections read Delta, the Greeks tab turned out to be hidden
on the Bitcoin screen by a gate written before Bitcoin greeks existed
(_api_greeks_venue, 20 Sep), leaving a finished screen with no way to open it.

The page's own functions are run in node; no network, no account.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import types

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config
import nbs_site
import user_delta
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. DELTA INDIA'S FIXED RATE")
check("the rate is Delta's own, 85 rupees to the dollar", config.DELTA_USD_INR == 85.0)
check("dollars become the rupees Delta's screen shows", config.usd_to_inr(12.35) == 1049.75 and config.usd_to_inr(0) == 0.0)
check("no amount, no figure - not a zero", config.usd_to_inr(None) is None)


print("2. THE BROKER BLOCK THE HEADER AND THE AI TAB READ")
def broker(wallet, state="ok", market="crypto"):
    real = user_delta.summary
    user_delta.summary = lambda user: {"state": state, "detail": "", "connected": state == "ok", "wallet": wallet}
    try:
        h = web_server.Handler.__new__(web_server.Handler)
        return h._broker("me@example.invalid", market)
    finally:
        user_delta.summary = real

b = broker([{"asset": "USD", "available": 12.35, "balance": 13.0}])
check("a dollar wallet is reported in dollars AND rupees at the fixed rate",
      b["funds"]["asset"] == "USD" and b["funds"]["available"] == 12.35 and b["funds"]["inr"] == 1050 and b["funds"]["inr_rate"] == 85.0,
      b["funds"])
b = broker([{"asset": "USD", "available": 12.35, "balance": 13.0}, {"asset": "INR", "available": 1050.0, "balance": 1105.0}])
check("if Delta's own rupee row comes with it, that exact figure is used, not a conversion",
      b["funds"]["inr"] == 1050.0 and b["funds"]["asset"] == "USD", b["funds"])
b = broker([{"asset": "USD", "available": 12.35, "balance": 13.0}, {"asset": "INR", "available": 777.0, "balance": 800.0}])
check("...even when it differs from the conversion", b["funds"]["inr"] == 777.0, b["funds"])
b = broker([{"asset": "USD", "available": 12.35, "balance": 13.0}, {"asset": "INR", "available": None, "balance": None}])
check("a rupee row that carries no amount is not used - the conversion is", b["funds"]["inr"] == 1050, b["funds"])
b = broker([{"asset": "USDT", "available": 100.0, "balance": 100.0}])
check("a USDT wallet is converted the same way", b["funds"]["asset"] == "USDT" and b["funds"]["inr"] == 8500, b["funds"])
b = broker([{"asset": "INR", "available": 1050.0, "balance": 1105.0}])
check("a wallet with only rupees is left as it was (the funds check counts dollars)", b["funds"] is None, b["funds"])
b = broker([{"asset": "BTC", "available": 0.001, "balance": 0.001}])
check("a wallet with neither is shown no figure, not an invented one", b["funds"] is None)
b = broker(None, state="missing")
check("no keys: no funds and not connected", b["funds"] is None and b["connected"] is False)
b = broker([{"asset": "USD", "available": 0.0, "balance": 0.0}])
check("an empty wallet is a real zero, in both currencies", b["funds"]["available"] == 0.0 and b["funds"]["inr"] == 0, b["funds"])


print("3. THE PAGE SAYS IT")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
SRC = open(os.path.join(HERE, "web_server.py")).read()
if not NODE:
    check("node is available to run the page's own functions", False, "install node to run this section")
else:
    n0 = SRC.index("const num=(v,d=2)")
    n1 = SRC.index("const esc=s=>", n0)
    f0 = SRC.index("function fundsLabel(f){")
    f1 = SRC.index("\n}\n", f0) + 3
    prog = "const assert = require('assert');\n" + SRC[n0:n1] + SRC[f0:f1] + r'''
assert.strictEqual(fundsLabel({asset: "USD", available: 12.35, inr: 1050}), "USD 12.35 (₹1,050)", "dollars, then the rupees beside them");
assert.strictEqual(fundsLabel({asset: "USD", available: 1234567.891, inr: 104938270}), "USD 12,34,567.89 (₹10,49,38,270)", "big amounts group the way the page's num() always has - the chip's dollars did too, before this");
assert.strictEqual(fundsLabel({asset: "USDT", available: 100, inr: 8500}), "USDT 100.00 (₹8,500)");
assert.strictEqual(fundsLabel({asset: "USD", available: 0, inr: 0}), "USD 0.00 (₹0)", "a real zero is shown as one");
assert.strictEqual(fundsLabel({asset: "USD", available: 12.35}), "USD 12.35", "no rupee figure: dollars alone, no NaN");
assert.strictEqual(fundsLabel({asset: "INR", available: 25000.4}), "₹25,000", "Zerodha's rupees are unchanged");
assert.strictEqual(fundsLabel({asset: "INR", available: 25000.4, inr: 5}), "₹25,000", "a rupee wallet never gets a second figure");
console.log("ok:label");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    check("the label reads dollars with the rupees beside them, and Zerodha's rupees are unchanged", "ok:label" in r.stdout and r.returncode == 0, out[-500:])
check("the header chip and the AI tab both use it (one formatter, so they cannot disagree)",
      SRC.count("fundsLabel(") >= 3 and "${fundsLabel(br.funds)} available" in SRC and "${fundsLabel(brk.funds)} available for a premium" in SRC)

html = nbs_site.delta_connect_page("me@example.invalid", "ok", "keys accepted", user_id="57777926", since="20 Sep",
                                   wallet=[{"asset": "USD", "available": 12.35, "balance": 13.0},
                                           {"asset": "BTC", "available": 0.001, "balance": 0.001},
                                           {"asset": "INR", "available": 500.0, "balance": 500.0}])
rows = [r for r in html.split('<div class="row">') if r.startswith("<b>Wallet")]
by = {r.split("·")[1].split("</b>")[0].strip(): r for r in rows}
check("Delta's own page lists every wallet row it was given", set(by) == {"USD", "BTC", "INR"}, sorted(by))
check("the dollar row carries its rupee equivalent", "&#8377;1,050" in by["USD"], by["USD"])
check("a bitcoin row does not (a coin is not dollars)", "&#8377;" not in by["BTC"], by["BTC"])
check("a rupee row is not converted again", "&#8377;" not in by["INR"], by["INR"])


print("4. THE GREEKS TAB IS REACHABLE ON BITCOIN")
if NODE:
    g0 = SRC.index("function gateTabs(){")
    g1 = SRC.index("\n}\n", g0) + 3
    prog = r'''
const assert = require("assert");
let LAST = null, TAB = "home", GATED_FOR = null, MARKETS_CALLED = 0;
function showTab(n){ TAB = n; }
function markets_(){ MARKETS_CALLED++; }
const els = {};
const document = {querySelector(sel){ return els[sel] || (els[sel] = {hidden: false, dataset: {}}); }};
const tab = name => document.querySelector(`.menu .tab[data-tab="${name}"]`);
''' + SRC[g0:g1] + r'''
const INDEX_ONLY = ["sector", "market", "vol", "levels", "internals", "strength", "season", "screener"];
LAST = {market: "crypto", account: {}};
gateTabs();
assert.strictEqual(tab("greeks").hidden, false, "Greeks & IV is open on the Bitcoin screen - Delta sends its own greeks for it");
for(const n of INDEX_ONLY) assert.strictEqual(tab(n).hidden, true, n + " stays hidden on Bitcoin: it is an index-members screen");
TAB = "greeks";
gateTabs();
assert.strictEqual(TAB, "greeks", "someone on the Greeks tab is not thrown back to Home when the Bitcoin screen is gated");
TAB = "screener";
gateTabs();
assert.strictEqual(TAB, "home", "...but someone on a tab that IS hidden there still is");
LAST = {market: "nse_index", account: {}};
gateTabs();
for(const n of ["greeks"].concat(INDEX_ONLY)) assert.strictEqual(tab(n).hidden, false, n + " is open on the Indian screen");
console.log("ok:gate");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    check("the page's own tab gate, run for real: Greeks open on Bitcoin, the index-only tabs hidden, all open on the Indian screen",
          "ok:gate" in r.stdout and r.returncode == 0, out[-600:])

print("5. THE DELTA KEYS PAGE CAN BE SEEN")
import re
def undefined_vars(html):
    return sorted(set(re.findall(r"var\(--([a-z0-9-]+)", html)) - set(re.findall(r"--([a-z0-9-]+)\s*:", html)))
pages = {"zerodha, not connected": nbs_site.connect_page("u@example.invalid", "missing", "d"),
         "zerodha, connected": nbs_site.connect_page("u@example.invalid", "ok", "d", user_id="AB1234", since="09:05"),
         "delta, no keys": nbs_site.delta_connect_page("u@example.invalid", "missing", "d"),
         "delta, keys accepted": nbs_site.delta_connect_page("u@example.invalid", "ok", "d", user_id="1", since="x",
                                                             wallet=[{"asset": "USD", "available": 1.0, "balance": 1.0}])}
pages.update({"public " + path: fn() for path, fn in nbs_site.PAGES.items() if fn.__code__.co_argcount == 0})
for name, html in pages.items():
    check(f"{name}: every CSS variable the page uses is defined (an undefined one silently drops the whole declaration)",
          undefined_vars(html) == [], undefined_vars(html))
check("the Zerodha page sends the IP to Profile > IP Whitelist, where Zerodha keeps it, not 'your app'",
      "Profile, top right" in pages["zerodha, not connected"] and "your app &rarr; IP whitelist" not in pages["zerodha, not connected"])
fields = re.findall(r'<input name="api_(?:key|secret)"[^>]*style="([^"]*)"', pages["delta, no keys"])
check("the API key and secret boxes both have a visible border and their own fill",
      len(fields) == 2 and all("border:1px solid var(--bd)" in f and "background:var(--sunken)" in f for f in fields), fields)

print("DELTA DISPLAY TEST PASSED" if not fails else f"DELTA DISPLAY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
