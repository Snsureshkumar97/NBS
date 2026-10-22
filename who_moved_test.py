#!/usr/bin/env python3
"""Buyers as well as sellers on the option chain's drawing (the user, 22 Oct 2026: "the option chain in the signal tab
shows about sellers and support but not about buyers if it shows everything explain nicely on the diagram with live
data it will easy to understand the market if it goes up or down"). feeds._buildup reads open interest and the premium
together over ~15 minutes - buyers in / sellers in / sellers out / buyers out at each strike, and which way it all
leans; the page draws them as pills on the ladder, a lean bar over it and plain words under it. No network."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import feeds

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra if not cond else ''}")
    if not cond:
        fails.append(name)


L = 1e5
SPOT = 23112.0
# 15 minutes ago: (call OI, put OI, call premium, put premium)
PAST = {23000.0: (40 * L, 100 * L, 160.0, 50.0), 23050.0: (35 * L, 40 * L, 125.0, 64.0),
        23100.0: (60 * L, 30 * L, 95.0, 80.0), 23150.0: (30 * L, 10 * L, 70.0, 104.0),
        23200.0: (80 * L, 8 * L, 50.0, 133.0), 23300.0: (66 * L, 5 * L, 24.0, 205.0),
        22500.0: (5 * L, 60 * L, 600.0, 4.0)}
NOW = {"strikes": [
    {"strike": 23000, "call_oi": 40.3 * L, "put_oi": 112 * L, "call_ltp": 190.0, "put_ltp": 38.0},   # puts: sellers in (+12); calls +0.75%: under 1%
    {"strike": 23050, "call_oi": 35 * L, "put_oi": 40 * L, "call_ltp": 150.0, "put_ltp": 48.0},     # no OI change: nothing
    {"strike": 23100, "call_oi": 68 * L, "put_oi": 27 * L, "call_ltp": 118.0, "put_ltp": 60.0},     # calls: buyers in (+8); puts: buyers out (-3)
    {"strike": 23150, "call_oi": 26 * L, "put_oi": 10 * L, "call_ltp": 88.0, "put_ltp": 80.0},      # calls: sellers out (-4)
    {"strike": 23200, "call_oi": 92 * L, "put_oi": 8 * L, "call_ltp": 49.5, "put_ltp": 132.0},     # calls: premium ~flat (-1%): nothing
    {"strike": 23300, "call_oi": 70 * L, "put_oi": 5 * L, "call_ltp": 20.0, "put_ltp": 205.0},     # calls: sellers in (+4)
    {"strike": 22500, "call_oi": 5 * L, "put_oi": 90 * L, "call_ltp": 640.0, "put_ltp": 3.0}]}     # 2.6% away: outside

print("1. WHO MOVED AT EACH STRIKE (feeds._buildup)")
bu = feeds._buildup(NOW, SPOT, PAST, 15 * 60)
rows = {r["strike"]: r for r in (bu or {}).get("rows", [])}
w = lambda k, side: (rows.get(k, {}).get(side) or {}).get("what")
check("contracts up, premium down: put sellers in at 23,000 (writing - support)", w(23000.0, "put") == "sellers_in", rows.get(23000.0))
check("contracts up, premium up: call buyers in at 23,100", w(23100.0, "call") == "buyers_in", rows.get(23100.0))
check("contracts down, premium down: put buyers out at 23,100", w(23100.0, "put") == "buyers_out", rows.get(23100.0))
check("contracts down, premium up: call sellers out at 23,150 (buying back)", w(23150.0, "call") == "sellers_out", rows.get(23150.0))
check("contracts up, premium down: call sellers in at 23,300 (a ceiling)", w(23300.0, "call") == "sellers_in", rows.get(23300.0))
check("open interest barely changed (23,050 not at all; 23,000's calls +0.75%, under the 1%): nothing said",
      23050.0 not in rows and w(23000.0, "call") is None, (rows.get(23050.0), rows.get(23000.0)))
check("premium barely moved (23,200 calls, -1%): nothing said, however many contracts", w(23200.0, "call") is None, rows.get(23200.0))
check("further than 2% from the price (22,500): left out", 22500.0 not in rows)
c = (rows.get(23100.0) or {}).get("call") or {}
check("each says how many contracts and how far the premium moved", c.get("oi_chg") == 8 * L and c.get("px_chg_pct") == 24.2, c)
up = 8 * L + 3 * L + 4 * L + 12 * L        # call buyers in, put buyers out, call sellers out, put sellers in
down = 4 * L                               # call sellers in
check("the lean: contracts betting on a rise against a fall", bu and bu["up"] == up and bu["down"] == down
      and bu["lean_up"] == round(up / (up + down), 3) and bu["minutes"] == 15, bu and (bu["up"], bu["down"], bu["lean_up"]))
check("no history, no premiums in it (an old sample), no chain, no price: None",
      feeds._buildup(NOW, SPOT, None) is None and feeds._buildup(None, SPOT, PAST) is None
      and feeds._buildup(NOW, None, PAST) is None
      and feeds._buildup(NOW, SPOT, {k: v[:2] for k, v in PAST.items()}, 900)["rows"] == [])

print("2. THE FEED KEEPS THE PREMIUMS WITH ITS SAMPLES")
class F:
    oi_hist = {}
f = F()
f._sellers = feeds.Feed._sellers.__get__(f)
f._who = feeds.Feed._who.__get__(f)
then = {"strikes": [{"strike": k, "call_oi": v[0], "put_oi": v[1], "call_ltp": v[2], "put_ltp": v[3]} for k, v in PAST.items()]}
f._sellers("NIFTY", then, SPOT)
check("one sample: nothing to compare with yet", f._who("NIFTY", NOW, SPOT) is None)
check("each sample carries both premiums", len(next(iter(f.oi_hist["NIFTY"][0][1].values()))) == 4)
f.oi_hist["NIFTY"][0] = (time.time() - 15 * 60, f.oi_hist["NIFTY"][0][1])
f._sellers("NIFTY", NOW, SPOT)
got = f._who("NIFTY", NOW, SPOT)
check("15 minutes on: who moved, compared with then", got and got["minutes"] == 15 and got["lean_up"] == bu["lean_up"], got)
src_feeds = open(os.path.join(HERE, "feeds.py")).read()
check("worked out with each reading and carried in the public one",
      'rec["buildup"] = self._who(name, oi, spot)' in src_feeds and '"buildup": rec.get("buildup"),' in src_feeds)

print("3. ON THE DRAWING AND IN WORDS (node)")
src = open(os.path.join(HERE, "web_server.py")).read()
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
else:
    n0 = src.index("const num=(v,d=2)")
    n1 = src.index("\n", src.index("const esc=s=>", n0)) + 1
    code = (src[n0:n1] + src[src.index("function sellersSay(se){"):src.index("function sellersRows(se){")]
            + src[src.index("const LADDER_EXIT"):src.index("function chainDraw(d){")])
    se = feeds._sellers_ahead({"strikes": [{"strike": k, "call_oi": v[0], "put_oi": v[1]} for k, v in
                                           ((s_["strike"], (s_["call_oi"], s_["put_oi"])) for s_ in NOW["strikes"])]}, SPOT)
    d = {"index": "NIFTY", "spot": SPOT,
         "rows": [{"strike": s_["strike"], "ce": {"oi": s_["call_oi"]}, "pe": {"oi": s_["put_oi"]}} for s_ in NOW["strikes"]]}
    even = dict(bu, lean_up=0.5)
    down_ = dict(bu, lean_up=0.2)
    prog = code + "\nconst X = " + json.dumps({"bu": bu, "se": se, "d": d, "even": even, "down": down_}) + r''';
const getComputedStyle = () => ({});
const o = {};
o.wide = ladderDraw(X.d, X.se, null, 680, null, X.bu);
o.narrow = ladderDraw(X.d, X.se, null, 340, null, X.bu);
o.none = ladderDraw(X.d, X.se, null, 680, null, null);
o.even = buildupSay(X.even); o.down = buildupSay(X.down); o.nobu = buildupSay(null);
o.quiet = leanBar({minutes: 4, rows: [], up: 0, down: 0, lean_up: null});
console.log(JSON.stringify(o));
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        print(r.stdout[-1500:], r.stderr[-1500:])
        o = {}
    wv = (o.get("wide") or {}).get("svg", "")
    check("pills on the ladder: buyers in (accent) at 23,100's calls, sellers in at 23,000's puts, sellers out at 23,150's calls",
          re.search(r'class="ocl-whot b">buyers in<', wv) and re.search(r'class="ocl-whot s put">sellers in<', wv)
          and re.search(r'class="ocl-whot s call out">sellers out<', wv) and re.search(r'class="ocl-whot b out">buyers out<', wv), wv[:300])
    check("a call's pill on the calls' side of the strike column, a put's on the puts' side",
          re.search(r'<text x="([\d.]+)" y="[\d.]+" text-anchor="middle" class="ocl-whot b">buyers in', wv)
          and float(re.search(r'<text x="([\d.]+)" y="[\d.]+" text-anchor="middle" class="ocl-whot b">buyers in', wv).group(1)) < 340
          and float(re.search(r'<text x="([\d.]+)" y="[\d.]+" text-anchor="middle" class="ocl-whot s put">sellers in', wv).group(1)) > 340)
    # 23,300's calls: the second-heaviest call level (its "sellers ... L" label) and a "sellers in" pill - the label moves past the pill
    m_pill = re.search(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="16" rx="8" class="ocl-who s call"/>', wv)
    m_lab = re.search(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)" height="16" rx="3" class="ocl-bg"/><text x="[\d.]+" y="[\d.]+" text-anchor="end" class="ocl-lab call">sellers 70\.0 L', wv)
    check("a level's label sits past its pill, not on it", m_pill and m_lab
          and float(m_lab.group(1)) + float(m_lab.group(2)) <= float(m_pill.group(1)) + 0.5, (m_pill and m_pill.groups(), m_lab and m_lab.groups()))
    nv = (o.get("narrow") or {}).get("svg", "")
    check("each pill on a solid background of its own, so it reads over a dark bar",
          wv.count('rx="8" class="ocl-bg"/><rect') == wv.count('class="ocl-who ') == 5, wv.count('class="ocl-who '))
    check("on a phone the pills are short: B+ / S+ / S− / B−", ">B+<" in nv and ">S+<" in nv and ">S−<" in nv and ">B−<" in nv)
    lean = (o.get("wide") or {}).get("lean", "")
    check("the lean bar: rise against fall, as a two-colour bar with both shares",
          'class="bu-lean"' in lean and "▲ rise 87%" in lean and "▼ fall 13%" in lean and 'style="width:87%"' in lean, lean)
    b = (o.get("wide") or {}).get("bsay", "")
    check("in words, the lean first", b.startswith("In the last 15 minutes, most of the positions that changed (87%) bet on a rise."), b)
    check("who bet on a rise, biggest first and grouped",
          "On a rise: put sellers added at 23,000 (+12.0 lakh); call buyers came in at 23,100 (+8.0 lakh); call sellers bought back at 23,150 (−4.0 lakh)." in b, b)
    check("who bet on a fall", "On a fall: call sellers added at 23,300 (+4.0 lakh)." in b, b)
    check("and what who_moved_study.py found: it repeats the last 15 minutes, it does not tell the next",
          b.endswith("In our test on 26 trading days, this lean matched the last 15 minutes' own move 95% of the time and "
                     "called the next 15 minutes right only about half the time (49.7%): it describes what just happened, "
                     "not what comes next."), b)
    check("evenly split, and a fall, said as such", "split about evenly between a rise and a fall" in o.get("even", "")
          and "most of the positions that changed (80%) bet on a fall." in o.get("down", ""), (o.get("even", "")[:120], o.get("down", "")[:120]))
    nl = (o.get("none") or {}).get("lean", "")
    check("no reading yet (just after a restart): no pills, no bar, no words - but a line saying why, not nothing",
          "ocl-who" not in (o.get("none") or {}).get("svg", "x") and "bu-bar" not in nl and o.get("nobu") == ""
          and "needs a few minutes of option data first" in nl, nl)
    qv = o.get("quiet", "")
    check("a reading with nothing big in it: says so, with its minutes", "bu-bar" not in qv and "last 4 min" in qv
          and "nothing big yet - no strike near the price has had its open contracts change by 1% or more" in qv, qv)
    kw, kn = (o.get("wide") or {}).get("key", ""), (o.get("narrow") or {}).get("key", "")
    check("a small key to the pills: all four, each with what it means",
          kw.count('class="bu-ki"') == 4 and ">buyers in</i><span>more contracts, option price up: <b>new buyers</b>" in kw
          and ">sellers in</i><span>more contracts, option price down: <b>new sellers</b>" in kw
          and ">sellers out</i><span>fewer contracts, option price up: <b>sellers buying back</b>" in kw
          and ">buyers out</i><span>fewer contracts, option price down: <b>buyers leaving</b>" in kw
          and "Blue: buyers. Sellers: red on the calls side, green on the puts side. Dotted: they left." in kw, kw)
    check("on a phone the key shows the same short pills the drawing does", ">B+</i>" in kn and ">S+</i>" in kn
          and ">S−</i>" in kn and ">B−</i>" in kn and ">buyers in</i>" not in kn)
    check("no reading at all: no key either", (o.get("none") or {}).get("key") == "")
    check("never a call to buy or sell", not any(x in json.dumps(o).lower() for x in ("buy now", "sell now", "you should")))

check("readable pills: a pill and its text are styled apart, a left pill is outlined, never filled with its text's colour",
      ".ocl .ocl-who.out{fill:var(--surface);fill-opacity:1;stroke-dasharray:3 2}" in src
      and src.index(".ocl .ocl-who.out{") > src.index(".ocl .ocl-who.put{") and ".ocl .ocl-whot.call{fill:var(--down)}" in src)

print("4. ON THE PAGE")
check("the Signal box: the lean bar over the drawing, who moved in words under it",
      '<div class="rr-h" data-h></div><div data-l></div><div class="sl-draw" data-d></div><div data-w></div>' in src
      and "set(lb, x.lean + x.key);" in src and '<p class="se-say se-who">' in src and "ladderSpot(d), r && r.buildup);" in src)
check("the Option chain tab too", "x.lean + x.key + x.svg" in src and "ladderSpot(d), (((LAST && LAST.indices) || {})[d.index] || {}).buildup);" in src)
check("named for both now", src.count("Buyers and sellers around the price") == 2 and "Sellers above &middot; support below${tr" not in src)
check("the note explains the pills", "Pills: who changed their positions at that strike in the last 15 minutes" in src)

print()
print("WHO MOVED TEST PASSED" if not fails else f"WHO MOVED TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
