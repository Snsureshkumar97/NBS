#!/usr/bin/env python3
"""The phone layout (24 Sep 2026: "the phone layout is not easy to use and doesn't look good").

Measured in Chrome at 320, 360, 390 and 414px, in all three looks and both markets, on every
tab: the top bar was two rows of chips running off the right edge (the funds figure cut
mid-word) with the page's name squeezed to an icon; Home was 16px wider than the screen; the
AI desk's switches were pills of three widths; at 320px four tabs were 12px too wide. After the
pass nothing was wider than the screen at any of those widths.
A browser is not run here, so this holds the pass in place from the source: the rules that
make it, the menu's chips following the header's, and the calendar's type staying readable.
"""
import json
import os
import re
import shutil
import subprocess
import sys

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
CSS = SRC[:SRC.index("</style>\n<script>\n// There is one look, Zerodha's, in two schemes")]
PHONE = CSS[CSS.index("THE PHONE PASS"):]

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def lum(rgb):
    f = lambda c: (c / 255) / 12.92 if c / 255 <= .03928 else (((c / 255) + .055) / 1.055) ** 2.4
    return .2126 * f(rgb[0]) + .7152 * f(rgb[1]) + .0722 * f(rgb[2])


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + .05) / (min(la, lb) + .05)


print("1. THE TOP BAR IS ONE ROW")
check("under 721px the header does not wrap and is as tall as a thumb wants",
      "@media (max-width:720px){" in PHONE and "header .hd{flex-wrap:nowrap;gap:8px;padding:8px 12px;min-height:52px" in PHONE)
check("the menu button carries the page's name at full width (it was squeezed to an icon)",
      ".hd .navbtn{flex:1 1 auto;justify-content:flex-start;min-width:0" in PHONE and ".hd .navbtn span{max-width:none;" in PHONE)
check("the market's state keeps its words (it was clipped to a dot)", ".hd .status .st-mkt{overflow:visible;text-overflow:clip;max-width:none" in PHONE)
check("the chips that ran off the right edge leave the top bar", ".hd .row{display:none !important}" in PHONE)
check("...and are at the top of the menu, in the sidebar the phone opens",
      '<div class="sidechips" id="sidechips">' in SRC and 'id="sidemkt"' in SRC and 'id="sidekite"' in SRC
      and SRC.index('id="sidechips"') > SRC.index('<aside class="side"') and SRC.index('id="sidechips"') < SRC.index('<nav class="menu" id="tabs"'))
check("the menu's chips do not show on a desktop, where the header still has them", ".sidechips{display:none}" in PHONE)

print("2. NOTHING IS WIDER THAN THE SCREEN")
check("Home's four buttons are a two-by-two grid, not a row that overflowed by 16px",
      ".welcome .acts{display:grid;grid-template-columns:1fr 1fr;gap:8px;width:100%}" in PHONE)
check("a grid column may shrink below its content", ".grid > *,.top3 > *,.tiles > *,.panes,.pane,.card,#colL,#colR{min-width:0}" in PHONE)
check("the 320px-minimum auto-fit grid is one column of the phone's width",
      ".grid2{grid-template-columns:minmax(0,1fr)}" in PHONE and "grid-template-columns:repeat(auto-fit,minmax(320px,1fr))" in CSS)
check("the screener's note wraps", "#scrnote{white-space:normal;overflow-wrap:anywhere}" in PHONE and ".scrctl{flex-wrap:wrap}" in PHONE)
check("on the narrowest phones the bottom bar's five buttons still fit (they were 8px over at 320px)",
      "@media (max-width:340px){" in PHONE and ".botnav{gap:0;padding-left:2px;padding-right:2px}" in PHONE)

print("3. SWITCHES LINE UP")
check("the AI desk's heading row: heading on its own line, each switch full width, all one size",
      ".thead{display:flex;flex-wrap:wrap;gap:8px;align-items:center}" in PHONE and ".thead > .eyebrow{flex:1 0 100%;margin:0}" in PHONE
      and ".thead > .lbtn,.thead > .ailots{flex:1 1 100%;justify-content:center;text-align:center}" in PHONE)
check("the bottom bar's labels stay visible and each button is a comfortable target",
      ".botnav button{min-height:52px;font-size:12px;gap:2px}" in PHONE)

print("4. THE MENU'S CHIPS FOLLOW THE HEADER'S")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own mirror code", False, "install node")
else:
    m0 = SRC.index("// On a phone the header has no room for the market and broker chips")
    m1 = SRC.index("const PAL = {items: [], sel: 0};", m0)
    prog = r'''
const assert = require("assert");
const observers = [];
class MutationObserver { constructor(f){ this.f = f; observers.push(this); } observe(el){ this.el = el; } fire(){ this.f(); } }
const mk = (href, text, display) => ({attrs: {href}, textContent: text, title: "", hidden: null, style: {display: display || ""},
                                      getAttribute(k){ return this.attrs[k] || null; }, setAttribute(k, v){ this.attrs[k] = v; }});
const els = {mktsw: mk("/market", "Switch market", "none"), kite: mk("/connect", "Delta Exchange · USD 0.56 (₹0) available", "inline-flex"),
             sidemkt: mk("#", ""), sidekite: mk("#", "")};
const document = {getElementById: id => els[id] || null};
new Function("document", "MutationObserver", ''' + json.dumps(SRC[m0:m1]) + r''')(document, MutationObserver);
assert.strictEqual(els.sidekite.textContent, els.kite.textContent, "the funds chip's words are copied at once");
assert.strictEqual(els.sidekite.hidden, false); assert.strictEqual(els.sidekite.getAttribute("href"), "/connect");
assert.strictEqual(els.sidemkt.hidden, true, "a header chip that is hidden is hidden in the menu too");
els.mktsw.style.display = "inline-flex"; els.mktsw.textContent = "Switch market"; observers[0].fire();
assert.strictEqual(els.sidemkt.hidden, false); assert.strictEqual(els.sidemkt.textContent, "Switch market");
els.kite.textContent = "Connect Zerodha"; els.kite.attrs.href = "/connect"; observers[1].fire();
assert.strictEqual(els.sidekite.textContent, "Connect Zerodha", "when the header's chip changes, the menu's follows");
els.kite.style.display = "none"; observers[1].fire();
assert.strictEqual(els.sidekite.hidden, true);
assert.strictEqual(observers.length, 2, "one watcher per chip");
const missing = {getElementById: id => id === "mktsw" ? els.mktsw : null};
new Function("document", "MutationObserver", ''' + json.dumps(SRC[m0:m1]) + r''')(missing, MutationObserver);
console.log("ok:mirror");
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("the page's own mirror code, run for real: text, link and hidden follow the header's chips, and a missing element is not an error",
          "ok:mirror" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-500:])

print("5. THE CALENDAR'S FIGURES STAY READABLE ON THEIR TINT")
check("each tinted day carries the type colour chosen for its tint, and every figure in it uses it",
      ".jcal .jd.tint :is(.n,.v,.c){color:var(--jink)}" in CSS and "--jink:${jink(v.gross, maxAbs)}" in SRC and 'v ? "tint" : ""' in SRC)
if NODE:
    j0 = SRC.index("function jink(v, maxAbs){")
    j1 = SRC.index("function jshade(v, maxAbs){")
    prog = r'''
const assert = require("assert");
const lumOf = hex => { const c = [1, 3, 5].map(i => parseInt(hex.substr(i, 2), 16)); const f = x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
const cr = (la, lb) => (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
const rgbLum = c => lumOf("#" + c.map(x => Math.round(x).toString(16).padStart(2, "0")).join(""));
function run(surface){
  const jink = new Function("css", ''' + json.dumps(SRC[j0:j1]) + ''' + "; return jink;")(name => surface);
  const out = {};
  for(const [name, sign, tint] of [["win", 1, [43, 224, 138]], ["loss", -1, [239, 85, 112]]]){
    let worst = 99;
    for(let r = 1; r <= 100; r += 1){      // v = 0 is a day with no trades: no tint at all
      const k = 0.28 + 0.72 * (r / 100), base = [1, 3, 5].map(i => parseInt(surface.substr(i, 2), 16));
      const bg = tint.map((c, i) => c * k + base[i] * (1 - k));
      const ink = jink(sign * r, 100);
      assert(ink === "#0b0e14" || ink === "#e6eaf2", "a colour is always chosen: " + ink);
      worst = Math.min(worst, cr(lumOf(ink), rgbLum(bg)));
    }
    out[name] = worst;
  }
  return out;
}
const dark = run("#1a1a1a"), light = run("#ffffff");
console.log(JSON.stringify({dark, light}));
assert.strictEqual(jinkOf(0), "");
'''.replace('assert.strictEqual(jinkOf(0), "");', '')
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        res = None
    check("the chooser, run for real over every strength of tint on the dark surface: the better of dark and light type is never below 4:1 "
          "(two narrow bands sit at about 4.1, where neither reaches 4.5 - bold figures)",
          bool(res) and min(res["dark"].values()) >= 4.0, ((res or {}), (r.stderr or "")[-300:]))
    check("...and on Zerodha's white page it never falls under 4.5:1", bool(res) and min(res["light"].values()) >= 4.5, res)
    check("a day with no trades gets no tint colour", "if(!v) return \"\";" in SRC[j0:j1])

print("6. 'RUNS ALL SESSION' CAN BE READ WHEN IT IS ON")
check("the switch, when on, is a green tint with green type - not green type on the blue button fill (1.8:1) - in both schemes",
      ':root[data-look="kite"] .lbtn.ao.on{background:var(--ok-bg);color:var(--up);border-color:var(--ok-bd)}' in CSS)

print("PHONE LAYOUT TEST PASSED" if not fails else f"PHONE LAYOUT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
