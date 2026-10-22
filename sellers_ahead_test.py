#!/usr/bin/env python3
"""Sellers above / support below from the option chain (the user, 21 Oct 2026: "how can we know the market will go up
till there and over there if lot of sellers are sitting ... from option chain"). 21 Oct's real Nifty chain, as
recorded: at 09:21 (spot 23,072) 67.4 lakh calls at 23,100 and 79.9 lakh at 23,200; by 09:40 +21.8 / +12.8 lakh more,
and 23,150 +14.7 lakh - the market stopped at 23,121. Display only. No network."""
import os
import sys
import tempfile
import time

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feeds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


L = 1e5
at_0921 = {"strikes": [
    {"strike": 22900, "call_oi": 30 * L, "put_oi": 45 * L}, {"strike": 23000, "call_oi": 40 * L, "put_oi": 100.7 * L},
    {"strike": 23050, "call_oi": 35 * L, "put_oi": 40 * L}, {"strike": 23100, "call_oi": 67.4 * L, "put_oi": 30 * L},
    {"strike": 23150, "call_oi": 30 * L, "put_oi": 10 * L}, {"strike": 23200, "call_oi": 79.9 * L, "put_oi": 8 * L},
    {"strike": 23300, "call_oi": 65.9 * L, "put_oi": 5 * L}, {"strike": 22800, "call_oi": 10 * L, "put_oi": 50.4 * L},
    {"strike": 22700, "call_oi": 5 * L, "put_oi": 66.6 * L}]}
past = {float(s["strike"]): (s["call_oi"], s["put_oi"]) for s in at_0921["strikes"]}
at_0940 = {"strikes": [dict(s) for s in at_0921["strikes"]]}
for s in at_0940["strikes"]:
    s["call_oi"] += {23100: 21.8 * L, 23150: 14.7 * L, 23200: 12.8 * L}.get(s["strike"], 0)

print("1. 21 OCT, NIFTY")
se = feeds._sellers_ahead(at_0921, 23072.0)
c, p = se["call"], se["put"]
check("sellers above: 23,100 and 23,200 - the two heaviest within 1.5%, nearest first",
      [l["strike"] for l in c["levels"]] == [23100.0, 23200.0] and c["levels"][0]["oi"] == 67.4 * L, c)
check("support below: 23,000 (100.7 lakh) first, then 22,800 (22,700 is 1.6% away, outside the 1.5% range)",
      [l["strike"] for l in p["levels"]] == [23000.0, 22800.0], p)
check("no history yet: no change, no 'adding'", c["levels"][0]["chg"] is None and not c["adding"] and se["minutes"] is None)
se = feeds._sellers_ahead(at_0940, 23112.0, past, 19 * 60)
c = se["call"]
check("by 09:40: 23,200 (92.7 lakh) / 23,300 the two heaviest above 23,112, with what was added at 23,200",
      [l["strike"] for l in c["levels"]] == [23200.0, 23300.0] and round(c["levels"][0]["chg"] / L, 1) == 12.8, c)
check("calls near the price up 15%+ in the window: 'sellers adding'", c["adding"] and c["band_pct"] >= 0.15 and se["minutes"] == 19, c)
check("puts near the price not growing: no 'support adding'", not se["put"]["adding"], se["put"])

print("2. NOTHING TO SHOW")
check("no chain (crypto, or none yet): None", feeds._sellers_ahead(None, 23000.0) is None
      and feeds._sellers_ahead({"strikes": []}, 23000.0) is None and feeds._sellers_ahead(at_0921, None) is None)
check("strikes too far away (beyond 1.5%): none on that side",
      feeds._sellers_ahead({"strikes": [{"strike": 24000, "call_oi": 9 * L, "put_oi": 1 * L}]}, 23000.0) is None)

print("3. THE FEED SAMPLES A MINUTE, KEEPS ~20")
class F:
    oi_hist = {}
f = F()
f._sellers = feeds.Feed._sellers.__get__(f)
first = f._sellers("NIFTY", at_0921, 23072.0)
check("the first sample: levels, no change yet", first and first["minutes"] is None and len(f.oi_hist["NIFTY"]) == 1)
f._sellers("NIFTY", at_0940, 23112.0)
check("a second call within a minute adds no sample", len(f.oi_hist["NIFTY"]) == 1)
f.oi_hist["NIFTY"][0] = (time.time() - 16 * 60, f.oi_hist["NIFTY"][0][1])
out = f._sellers("NIFTY", at_0940, 23112.0)
check("16 minutes on: compared with then, said as 16 min", out["minutes"] == 16 and out["call"]["adding"], out)
f.oi_hist["NIFTY"].insert(0, (time.time() - 30 * 60, {}))
f.oi_hist["NIFTY"].append((time.time() - 61, {}))
f._sellers("NIFTY", at_0940, 23112.0)
check("samples older than ~20 minutes are dropped", all(time.time() - t <= 20 * 60 + 5 for t, _ in f.oi_hist["NIFTY"]))

print("4. ON THE PAGE")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
home = src[src.index('<section class="pane on" data-pane="home">'):src.index("</section>", src.index('data-pane="home"'))]
chain = src[src.index('<section class="pane" data-pane="chain">'):src.index("</section>", src.index('data-pane="chain"'))]
check("off the Dashboard since 21 Oct 2026 (\"remove the thing that show in the dash board\"), on the Option chain tab",
      'id="kdsellers"' not in home and "function sellersDash(" not in src and 'id="ocsellers"' in chain
      and "try{ ocSellersDraw(d); }catch(e){}" in src)
check("the Signal page has its own box for them, drawn for every system (not inside room to run): the drawing beside the "
      "trade since 22 Oct 2026, the short table until the chain comes",
      'id="sigsellers"' in src and "  roomRun(r);\n  sigSellers(r);" in src and "set(wd, sellersRows(se));" in src)
check("the public reading carries them", '"sellers": rec.get("sellers"),' in open(os.path.join(os.path.dirname(
      os.path.abspath(__file__)), "feeds.py")).read())

print("5. IN PLAIN WORDS (the user: \"can the tool explain in a simple way about that option data\")")
import json
import shutil
import subprocess
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
else:
    n0 = src.index("const num=(v,d=2)")
    n1 = src.index("\n", src.index("const esc=s=>", n0)) + 1
    fn = src[src.index("function sellersSay(se){"):src.index("function sellersRows(se){")]
    cases = {
        "adding": feeds._sellers_ahead(at_0940, 23112.0, past, 15 * 60),
        "plain": feeds._sellers_ahead(at_0921, 23072.0),
        "leaving": {"call": {"levels": [{"strike": 23100, "oi": 40 * L, "dist": 30, "chg": -5 * L}], "adding": False},
                    "put": {"levels": [{"strike": 23000, "oi": 60 * L, "dist": 70, "chg": -6 * L}], "adding": False}},
        "support_heavy": {"call": {"levels": [{"strike": 23100, "oi": 20 * L, "dist": 30, "chg": 0}], "adding": False},
                          "put": {"levels": [{"strike": 23000, "oi": 90 * L, "dist": 70, "chg": 0}], "adding": True}},
        "none": None,
    }
    prog = src[n0:n1] + fn + "\nconst C = " + json.dumps(cases) + ";\nconst o = {};\nfor (const k in C) o[k] = sellersSay(C[k]);\nconsole.log(JSON.stringify(o));"
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        o = {}
        print(r.stdout, r.stderr)
    a = o.get("adding", "")
    check("calls being added near the price: the nearest level named, 'may struggle to get past it'",
          a.startswith("Sellers are waiting at 23,200 (92.7 lakh calls, 88 points above), and more are being added right now")
          and "may struggle to get past it" in a, a)
    check("support named with its size and distance", "Support at 23,000 (100.7 lakh puts, 112 points below)" in a, a)
    check("both sides about even (158.6 vs 151.1 lakh): no 'looks harder' sentence", "looks harder" not in a, a)
    pl = o.get("plain", "")
    check("nothing changing: the plain 'often slows down' / 'often bounces'", "often slows down" in pl and "often bounces" in pl, pl)
    lv = o.get("leaving", "")
    check("sellers and put writers leaving: 'ceiling is getting weaker', 'support ... getting weaker'",
          "that ceiling is getting weaker" in lv and "is getting weaker: put sellers are leaving" in lv, lv)
    sh = o.get("support_heavy", "")
    check("put writers adding, far more support: 'getting stronger', 'falling looks harder'",
          "is getting stronger" in sh and "falling looks harder than rising" in sh, sh)
    check("no data: nothing said", o.get("none") == "")
    check("never a call to buy or sell", not any(w in json.dumps(o).lower() for w in ("buy now", "sell now", "you should")))
check("shown in the Signal box and under the option chain's drawing",
      "sellersSay(se) ? `<p class=\"se-say\">" in src and "const say = sellersSay(se);" in src)
print("6. THE OPTION CHAIN'S DRAWING (node)")
if NODE:
    a = src.index("const LADDER_EXIT")
    z = src.index("function chainDraw(d){")
    se0940 = feeds._sellers_ahead(at_0940, 23112.0, past, 15 * 60)
    rows = [{"strike": s_["strike"], "ce": {"oi": s_["call_oi"]}, "pe": {"oi": s_["put_oi"]}} for s_ in at_0940["strikes"]]
    prog2 = (src[n0:n1] + src[src.index("function sellersSay(se){"):src.index("function sellersRows(se){")] + src[a:z]
             + "\nconst els = {}; const $ = id => els[id] || (els[id] = {innerHTML: ''});\n"
             + "const getComputedStyle = () => ({});\n"
             + "let LAST = {indices: {NIFTY: {sellers: " + json.dumps(se0940) + "}}};\n"
             + "ocSellersDraw({index: 'NIFTY', spot: 23112, rows: " + json.dumps(rows) + "});\n"
             + "console.log(JSON.stringify(els.ocsellers.innerHTML));")
    r2 = subprocess.run([NODE, "-e", prog2], capture_output=True, text=True, timeout=60)
    try:
        h = json.loads(r2.stdout.strip().splitlines()[-1])
    except Exception:
        h = ""
        print(r2.stdout, r2.stderr)
    check("a drawing: calls written left, puts written right, the price line", "<svg class=\"ocl\"" in h
          and "calls written (sellers)" in h and "puts written (support)" in h and "price 23,112" in h, h[:200])
    check("the sellers levels named with what was added: 23,200 +12.8 lakh", "sellers 92.7 L +12.8" in h, h[-600:])
    check("the support levels named", "support 100.7 L" in h)
    check("the plain words under it, and what the bars mean", "<p class=\"se-say\">" in h and "act like a ceiling" in h)
check("the chain table's strike column is tagged too", 'small class="kmark sell">sellers' in src and 'small class="kmark supp">support' in src)

print()
print("SELLERS AHEAD TEST PASSED" if not fails else f"SELLERS AHEAD TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
