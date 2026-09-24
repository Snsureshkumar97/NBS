#!/usr/bin/env python3
"""The Zerodha look (data-look="kite"), asked for on 24 Sep 2026:
"a Zerodha kind of theme ... a switch that can change the theme ... I don't want animation
effects, pure kind of Zerodha".

Source checks, the contrast arithmetic on the real token values, and the page's own
switch code run in node. The rendered look was checked in Chrome (390px, 1280px, both
markets, every tab) with a script that lists every element whose text falls under the
contrast bar or whose background is still one of the dark look's: nothing was left over.
Because that was done by looking, this test keeps it from quietly coming undone: every
rule in the stylesheet that paints a dark surface or a light-on-dark colour must have a
Zerodha-look override.
"""
import os
import re
import shutil
import subprocess
import sys

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def lum(h):
    h = h.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


CSS = SRC[:SRC.index("</style>\n<script>\n// The look is set before anything paints")]
block = re.search(r':root\[data-look="kite"\]\{(.*?)\}', CSS, re.S)
tok = dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{6})", block.group(1))) if block else {}
KITE = CSS[CSS.index("THE ZERODHA LOOK"):]

print("1. IT IS KITE'S PALETTE")
check("the token block exists and says it is a light scheme", bool(tok) and "color-scheme:light" in block.group(1), sorted(tok)[:5])
check("a white page with Kite's light greys", tok["bg"] == "#ffffff" and tok["surface"] == "#ffffff" and tok["raised"] == "#f9f9f9")
check("Kite's blue for links and actions, its orange for the mark, its red for a loss",
      tok["accent"] == "#387ed1" and tok["brand"] == "#ff5722" and tok["down"] == "#df514c", (tok["accent"], tok["brand"], tok["down"]))
check("3px corners, as Kite has", "--r:3px" in block.group(1) and "--r-sm:3px" in block.group(1))

print("2. EVERY GREY AND EVERY SIGNAL COLOUR READS ON THE SURFACE IT SITS ON")
for ink in ("ink", "ink-2", "ink-3"):
    for sname in ("bg", "surface", "raised"):
        r = contrast(tok[ink], tok[sname])
        check(f"--{ink} {tok[ink]} on --{sname} {tok[sname]} clears 4.5:1", r >= 4.5, f"{r:.2f}:1")
for col in ("up", "down", "warn", "accent", "brand"):
    for sname in ("surface", "raised"):
        r = contrast(tok[col], tok[sname])
        check(f"--{col} {tok[col]} on --{sname} clears 3:1 (always paired with a sign or a word)", r >= 3.0, f"{r:.2f}:1")
r = contrast("#ffffff", tok["accent-strong"])
check(f"the filled buttons' blue {tok['accent-strong']} carries white type at 4.5:1", r >= 4.5, f"{r:.2f}:1")
r = contrast("#ffffff", "#c62828")
check("the live-orders-on red carries white type at 4.5:1", r >= 4.5, f"{r:.2f}:1")

print("3. NOTHING MOVES, BLURS, GLOWS OR CASTS A SHADOW")
glob = re.search(r':root\[data-look="kite"\] \*,:root\[data-look="kite"\] \*::before,:root\[data-look="kite"\] \*::after\{(.*?)\}', CSS, re.S)
body = glob.group(1) if glob else ""
for prop in ("animation:none", "transition:none", "text-shadow:none", "box-shadow:none", "backdrop-filter:none", "scroll-behavior:auto"):
    check(f"every element, whatever the rule below it says: {prop} !important",
          re.search(r"(?<![-\w])" + re.escape(prop) + r" !important", body) is not None)
check("the scene is hidden and the page has no perspective", ':root[data-look="kite"] #bg3d{display:none}' in CSS
      and ':root[data-look="kite"] .wrap{perspective:none}' in CSS)
check("no tilt, no sway on the signal card", ".herocard{transform:none !important}" in KITE)
check("the scene and the card tilt only ever start in the glass look",
      SRC.count('if(document.documentElement.dataset.look !== "glass") return;') == 2)
check("the world-markets strip is still and scrolls sideways under the thumb, not as a marquee",
      ".ticker{background:var(--raised);border-bottom:1px solid var(--bd);overflow-x:auto}" in KITE and ".tk-track{transform:none;width:max-content}" in KITE)
check("the menu opens without a slide (the drawer's transition is switched off with the rest)", "transition:none !important" in body)

print("4. NOTHING IS LEFT DARK OR PALE-ON-WHITE")
css_nc = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
rules = re.findall(r"([^{}@]+)\{([^{}]*)\}", css_nc)


def rl(r, g, b):
    f = lambda c: (c / 255) / 12.92 if c / 255 <= .03928 else (((c / 255) + .055) / 1.055) ** 2.4
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b)


def colours(v):
    out = []
    for c in re.findall(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", v):
        out.append((rl(*map(int, c[:3])), float(c[3]) if c[3] else 1.0))
    for h in re.findall(r"#([0-9a-fA-F]{6})\b", v):
        out.append((rl(*[int(h[i:i + 2], 16) for i in (0, 2, 4)]), 1.0))
    if v.strip() in ("#fff", "white"):
        out.append((1.0, 1.0))
    return out


dark, light = set(), set()
for sel, decl in rules:
    sel = " ".join(sel.split())
    if "data-look" in sel:
        continue
    for m in re.finditer(r"background(?:-color|-image)?\s*:\s*([^;]+)", decl):
        v = m.group(1)
        if "gradient" in v or any(a >= .4 and l < .12 for l, a in colours(v)):
            dark.add(sel)
    for m in re.finditer(r"(?<![-\w])color\s*:\s*([^;]+)", decl):
        v = m.group(1).strip()
        if not v.startswith("var(") and any(a >= .5 and l > .3 for l, a in colours(v)):
            light.add(sel)
# The only rules that need no override of their own, and why:
FINE = {".aipicker .lbtn.on .pl": "white type inside .aipicker .lbtn.on, which the Zerodha block fills with blue",
        ".key.dash": "a legend swatch drawn in --vwap, a token",
        ".notice.risk .more": "covered by `.notice.risk :is(b,.more,summary b)`",
        ".notice.risk b": "covered by `.notice.risk :is(b,.more,summary b)`"}
# the selectors the Zerodha block itself names (its :is(...) groups flattened), so a mention in a comment
# or in a longer selector for something else does not count
kite_rules = [(sel, decl) for sel, decl in re.findall(r"([^{}@]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", KITE, flags=re.S)) if 'data-look="kite"' in sel]
named = set()
for sel, _ in kite_rules:
    sel = " ".join(sel.replace(':root[data-look="kite"]', "").split())
    for part in re.split(r"[,()]", sel):
        named.add(re.sub(r"^:is", "", part).strip().split(":hover")[0])
missing = []
for sel in sorted(dark | light):
    for part in [p.strip() for p in sel.split(",")]:
        needle = part.split(":hover")[0].split("::")[0]
        if needle in ("body", "header", "body::before") or part in FINE:
            continue
        if needle not in named:
            missing.append(part)
check("every base rule that paints a dark surface or a pale-on-dark colour is named in the Zerodha block",
      not missing, sorted(set(missing))[:8])
check("the two notices are re-painted in Kite's pale tints (they are dark panels with light type in the other looks)",
      re.search(r'data-look="kite"\] \.notice\.risk\{background:#fff[0-9a-f]+', KITE) is not None
      and re.search(r'data-look="kite"\] \.notice\.stale\{background:#fff[0-9a-f]+', KITE) is not None)
check("the survey found the rules it should (so the test is not passing on an empty list)", len(dark) >= 25 and len(light) >= 8, (len(dark), len(light)))

print("5. THE SWITCH")
check("a three-way switch in the menu: Dark, Zerodha, Glass",
      'id="looksw"' in SRC and all(f'data-look="{k}">' in SRC for k in ("terminal", "kite", "glass"))
      and ">Dark</button>" in SRC and ">Zerodha</button>" in SRC and ">Glass</button>" in SRC)
check("...and the Home button names the look and cycles them", 'lb.textContent = "Theme: " + LOOKS[LOOK]' in SRC
      and "LOOK_ORDER = [\"terminal\", \"kite\", \"glass\"]" in SRC)
check("dark stays the default", 'document.documentElement.dataset.look = "terminal"; }' in SRC)
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own switch code", False, "install node")
else:
    h0 = SRC.index("// Three looks: terminal (dark, the default)")
    h1 = SRC.index("</script></head><body>", h0)
    head_js = SRC[SRC.index("try{", h0):h1]
    s0 = SRC.index("const LOOKS = {terminal:")
    s1 = SRC.index("// On a phone the header has no room for the market and broker chips")
    sw_js = SRC[s0:s1]
    prog = r'''
const assert = require("assert");
function head(stored, throws){
  const doc = {documentElement: {dataset: {}}};
  const localStorage = {getItem(){ if(throws) throw new Error("blocked"); return stored; }};
  new Function("document", "localStorage", %s)(doc, localStorage);
  return doc.documentElement.dataset.look;
}
assert.strictEqual(head(null), "terminal", "nothing saved: dark");
assert.strictEqual(head("kite"), "kite");
assert.strictEqual(head("glass"), "glass");
assert.strictEqual(head("terminal"), "terminal");
assert.strictEqual(head("neon"), "terminal", "an unknown value never reaches the page");
assert.strictEqual(head("KITE"), "terminal", "the value is exact");
assert.strictEqual(head("kite", true), "terminal", "storage that throws (private window): dark");
console.log("ok:head");

function page(look){
  const saved = {}; let reloaded = 0; const clicks = {};
  const mk = (data) => ({dataset: data || {}, classList: {t: {}, toggle(c, on){ this.t[c] = on; }}, attrs: {}, setAttribute(k, v){ this.attrs[k] = v; },
                         addEventListener(ev, f){ clicks[(data && data.look) || "btn"] = f; }, textContent: ""});
  const lookbtn = mk(); const btns = ["terminal", "kite", "glass"].map(k => mk({look: k}));
  const document = {documentElement: {dataset: {look}}, getElementById: id => id === "lookbtn" ? lookbtn : null,
                    querySelectorAll: sel => sel === "#looksw button[data-look]" ? btns : []};
  const localStorage = {setItem(k, v){ saved[k] = v; }};
  const location = {reload(){ reloaded++; }};
  const api = new Function("document", "localStorage", "location", %s + "; return {LOOK, LOOKS, LOOK_ORDER};")(document, localStorage, location);
  return {api, saved, get reloaded(){ return reloaded; }, clicks, lookbtn, btns};
}
let p = page("kite");
assert.strictEqual(p.api.LOOK, "kite");
assert.strictEqual(p.lookbtn.textContent, "Theme: Zerodha");
assert.strictEqual(p.btns[1].attrs["aria-pressed"], "true"); assert.strictEqual(p.btns[0].attrs["aria-pressed"], "false");
p.clicks["btn"]();
assert.strictEqual(p.saved["nbs.look.v1"], "glass", "the Home button goes on to the next look"); assert.strictEqual(p.reloaded, 1);
p = page("glass"); p.clicks["btn"]();
assert.strictEqual(p.saved["nbs.look.v1"], "terminal", "...and round again");
p = page("terminal"); p.clicks["btn"]();
assert.strictEqual(p.saved["nbs.look.v1"], "kite");
p = page("terminal"); p.clicks["kite"]();
assert.strictEqual(p.saved["nbs.look.v1"], "kite", "the switch in the menu chooses that look"); assert.strictEqual(p.reloaded, 1);
p = page("kite"); p.clicks["kite"]();
assert.strictEqual(p.reloaded, 0, "pressing the look you are already in does not reload the page");
p = page("junk");
assert.strictEqual(p.api.LOOK, "terminal", "an unknown look on the page reads as dark");
console.log("ok:switch");
''' % (repr(head_js).replace("\\'", "'") if False else __import__("json").dumps(head_js), __import__("json").dumps(sw_js))
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    check("the head script, run for real: dark unless kite or glass is saved, and safe with storage blocked", "ok:head" in r.stdout, out[-500:])
    check("the switch code, run for real: cycling, choosing, staying put, no needless reload", "ok:switch" in r.stdout and r.returncode == 0, out[-500:])


print("6. THE LAYOUT OF KITE ITSELF (25 Sep 2026: 'it should be like this' - the user's screenshot of Kite's dashboard)")
check("sections are flat and run on, separated by thin rules, not drawn as boxes",
      ':root[data-look="kite"] :is(.card,.session){background:transparent;border:0;border-bottom:1px solid var(--bd-soft);' in KITE
      and ':root[data-look="kite"] .herocard{background:transparent;border:0;border-bottom:1px solid var(--bd-soft)' in KITE)
# a :is() takes its most specific member, and .notice.stale once inflated the general rule so the flat rules below it silently lost:
general = re.search(r':root\[data-look="kite"\] :is\(([^)]*)\)\{\s*background:var\(--surface\);border:1px solid var\(--bd\)', KITE)
check("...the general rule names only single-class selectors", bool(general) and "notice" not in general.group(1) and
      all(part.count(".") == 1 for part in general.group(1).split(",")), general.group(1) if general else None)
check("type is set light, as Kite sets it", ":is(h1,h2,h3,.htitle,.welcome h1,.hero .v,.herocard #bias,.mkt .px,.tile .v){font-weight:400;letter-spacing:0}" in KITE)
check("on a wide screen the menu is a bar across the top: fixed, full width, 52px, white",
      "@media (min-width:901px){" in KITE and ":root[data-look=\"kite\"] .side{top:0;left:0;right:0;bottom:auto;width:auto;height:52px;flex-direction:row;" in KITE
      and ".main{margin-left:0;padding-top:52px}" in KITE and "header{top:52px}" in KITE)
check("its links are plain text, the current page in Kite's orange with an underline",
      ".menu .tab.on{background:transparent;border-color:transparent;color:var(--brand);" in KITE and "border-bottom-color:var(--brand)}" in KITE)
check("a group's pages drop down under its name; the group holding the current page is marked",
      ".mgrp-items{position:absolute;top:52px;right:0;min-width:230px;" in KITE and ".mgrp:has(.tab.on) > .mtoggle{color:var(--brand)" in KITE)
check("on a laptop width the wordmark goes and the padding closes up so the bar still fits (it ran off the screen at 901px)",
      "@media (max-width:1180px){" in KITE and ".sbrand > div{display:none}" in KITE)
check("the indices are a watchlist down the left, sticky, with the page beside it",
      "grid-template-columns:360px minmax(0,1fr)" in KITE and ".wrap > #markets{grid-column:1;grid-row:1 / span 6;position:sticky;" in KITE
      and '#markets::before{content:"Watchlist"' in KITE)
check("each index is a row: name and price on one line, expiry and signal state under; a signal colours the name and the edge",
      'grid-template-areas:"nm px" "ex st"' in KITE and ".mkt.bull .nm{color:var(--up)}" in KITE and ".mkt.bear .nm{color:var(--down)}" in KITE)
check("the strip under the bar carries plain text, not boxed chips", ".hd .status{background:transparent;border:0;padding:0}" in KITE)

if NODE:
    k0 = SRC.index("// In the Zerodha look on a wide screen the menu is a bar across the top")
    k1 = SRC.index("document.querySelectorAll(\".mgrp\").forEach(g => {", k0)
    n0 = SRC.index("// The Zerodha look's bar across the top (wide screens).")
    n1 = SRC.index('$("navbtn").addEventListener("click", navOpen);', n0)
    prog = r"""
const assert = require("assert");
class El {
  constructor(tag, cls, data){ this.tag = tag; this.cls = (cls || "").split(" ").filter(Boolean); this.dataset = Object.assign({}, data || {});
    this.children = []; this.parent = null; this.L = {}; this.attrs = {}; this.childNodes = []; }
  get className(){ return this.cls.join(" "); }
  set innerHTML(html){                     // only the markup the bar builds for its More group
    this.children = [];
    const toggle = new El("button", "mgroup mtoggle"); const items = new El("div", "mgrp-items");
    toggle.parent = items.parent = this; this.children.push(toggle, items);
  }
  appendChild(el){ if(el.parent) el.parent.children = el.parent.children.filter(c => c !== el); el.parent = this; this.children.push(el); return el; }
  has(sel){
    if(sel === "a.tab") return this.tag === "a" && this.cls.includes("tab");
    const m = /^\.tab\[data-tab="(\w+)"\]$/.exec(sel); if(m) return this.cls.includes("tab") && this.dataset.tab === m[1];
    if(sel[0] === ".") return this.cls.includes(sel.slice(1));
    if(sel === "#tabs") return this.id === "tabs";
    return false;
  }
  all(sel){ let out = []; for(const c of this.children){ if(c.has(sel)) out.push(c); out = out.concat(c.all(sel)); } return out; }
  querySelector(sel){ return this.all(sel)[0] || null; }
  querySelectorAll(sel){ return this.all(sel); }
  closest(sel){ for(let e = this; e; e = e.parent) if(e.has(sel)) return e; return null; }
  setAttribute(k, v){ this.attrs[k] = v; }
  addEventListener(ev, f){ (this.L[ev] = this.L[ev] || []).push(f); }
  fire(ev, target){ (this.L[ev] || []).forEach(f => f({target: target || this, key: ev})); }
}
const tab = (t, txt) => { const b = new El("button", "tab", {tab: t}); b.childNodes = [{nodeType: 1, textContent: ""}, {nodeType: 3, textContent: txt}]; return b; };
const bar = new El("nav", "menu"); bar.id = "tabs";
const home = tab("home", "Home"); bar.appendChild(home);
["signal", "chart", "tradingview", "chain", "watchlist", "journal", "marketbot", "aidesk"].forEach(t => bar.appendChild(tab(t, t)));
const groups = {};
["market", "analysis", "research"].forEach(g => { const grp = new El("div", "mgrp", {grp: g, open: "false"}); const tg = new El("button", "mgroup mtoggle"); const it = new El("div", "mgrp-items");
  grp.appendChild(tg); grp.appendChild(it); it.appendChild(tab(g + "1", g + " page")); bar.appendChild(grp); groups[g] = grp; });
const a1 = new El("a", "tab"), a2 = new El("a", "tab"); bar.appendChild(a1); bar.appendChild(a2); bar.appendChild(tab("admin", "Admin"));
const looksw = new El("div", "looksw"), foot = new El("div", "sidefoot");
const docL = {};
const document = {documentElement: {dataset: {look: LOOK}}, createElement: tag => new El(tag), querySelector: sel => sel === ".sidefoot" ? foot : null,
                  addEventListener: (ev, f) => { (docL[ev] = docL[ev] || []).push(f); }};
const winL = {};
const $ = id => id === "tabs" ? bar : id === "looksw" ? looksw : null;
const TAB_LABEL = {home: "Home"};
const addEventListener = (ev, f) => { (winL[ev] = winL[ev] || []).push(f); };
const matchMedia = q => ({matches: WIDE});
const api = new Function("document", "matchMedia", "$", "TAB_LABEL", "addEventListener", "bar", "groups",
  """ + __import__("json").dumps("") + r""" + %s + "\n" + %s + "\nreturn {navGroup, KITE_NAV};");
""" % (__import__("json").dumps(SRC[k0:k1]), __import__("json").dumps(SRC[n0:n1]))
    def run_case(look, wide):
        return prog.replace("LOOK", '"' + look + '"').replace("WIDE", "true" if wide else "false") + r"""
const out = (() => { try { return api(document, matchMedia, $, TAB_LABEL, addEventListener, bar, groups); } catch(e){ return {err: e.message + "\n" + e.stack}; } })();
if(out.err) { console.log("ERR " + out.err); process.exit(1); }
const kite = out.KITE_NAV;
const names = el => el.children.map(c => c.dataset.tab || c.dataset.grp || c.tag + "." + c.className);
console.log(JSON.stringify({kite, top: names(bar), more: bar.querySelector(".mgrp[data-grp=more]") ? 1 : 0, homeText: home.childNodes[1].textContent, label: TAB_LABEL.home}));
"""
    import json, subprocess
    # a Zerodha look on a wide screen builds the bar
    prog_k = run_case("kite", True)
    prog_k += r"""
const more = bar.children.find(c => c.dataset && c.dataset.grp === "more");
const moreItems = more.children[1].children.map(c => c.dataset.tab || c.tag + "." + c.className);
console.log(JSON.stringify({moreItems}));
// the groups are dropdowns opened by a click, one at a time
const open = g => g.dataset.open === "true";
out.navGroup(groups.market, true);                     // the page showing lives in it: NOT a reason to drop it down
console.log(JSON.stringify({openedByPage: open(groups.market)}));
out.navGroup(groups.market, true, true); bar.fire("click", groups.market.children[0]);
console.log(JSON.stringify({openedByClick: open(groups.market)}));
out.navGroup(groups.analysis, true, true); bar.fire("click", groups.analysis.children[0]);
console.log(JSON.stringify({afterSecond: {market: open(groups.market), analysis: open(groups.analysis)}}));
(docL.click || []).forEach(f => f({target: new El("div")}));
console.log(JSON.stringify({afterOutsideClick: {analysis: open(groups.analysis)}}));
out.navGroup(groups.research, true, true); (winL.keydown || []).forEach(f => f({key: "Escape"}));
console.log(JSON.stringify({afterEscape: open(groups.research)}));
out.navGroup(groups.market, true, true); bar.fire("click", groups.market.children[1].children[0]);
console.log(JSON.stringify({afterPickingAPage: open(groups.market)}));
"""
    r = subprocess.run([NODE, "-e", prog_k], capture_output=True, text=True, timeout=60)
    lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
    res = {}
    for l in lines:
        res.update(json.loads(l))
    ok_run = r.returncode == 0 and bool(res)
    check("the top bar's own code, run for real on a copy of the menu's structure: it runs", ok_run, ((r.stdout or "") + (r.stderr or ""))[-600:])
    if ok_run:
        check("the used-most pages stay on the bar, in order, with the three groups and More after them",
              res["top"][:6] == ["home", "signal", "chart", "chain", "journal", "aidesk"] and res["top"][6:] == ["market", "analysis", "research", "more"], res["top"])
        check("More holds the rest - TradingView, Watchlist, Ask TradePicker, the two account links, Admin - and the theme switch and sign-out",
              res["moreItems"] == ["tradingview", "watchlist", "marketbot", "a.tab", "a.tab", "admin", "div.looksw", "div.sidefoot"], res["moreItems"])
        check("Home is called Dashboard, on the bar and in the phone's title", res["homeText"] == "Dashboard" and res["label"] == "Dashboard")
        check("a dropdown is not opened by the page you are on living in it", res["openedByPage"] is False)
        check("...it opens when clicked", res["openedByClick"] is True)
        check("only one dropdown is open at a time", res["afterSecond"] == {"market": False, "analysis": True}, res["afterSecond"])
        check("a click anywhere else closes it", res["afterOutsideClick"] == {"analysis": False}, res["afterOutsideClick"])
        check("Escape closes it", res["afterEscape"] is False)
        check("choosing a page in it closes it", res["afterPickingAPage"] is False)
    # anything else leaves the menu as it was
    for look, wide in (("terminal", True), ("glass", True), ("kite", False)):
        r = subprocess.run([NODE, "-e", run_case(look, wide) + r"""
out.navGroup(groups.market, true);
console.log(JSON.stringify({openByPage: groups.market.dataset.open === "true"}));"""], capture_output=True, text=True, timeout=60)
        res2 = {}
        for l in r.stdout.splitlines():
            if l.startswith("{"):
                res2.update(json.loads(l))
        check(f"the {look} look{'' if wide else ' on a phone'} leaves the menu alone: same buttons in the same places, no More, Home still Home, "
              "and a group still opens for the page you are on",
              r.returncode == 0 and res2.get("kite") is False and "more" not in res2.get("top", []) and res2.get("homeText") == "Home"
              and res2.get("openByPage") is True and res2["top"][:3] == ["home", "signal", "chart"], (res2, (r.stderr or "")[-200:]))

print("KITE LOOK TEST PASSED" if not fails else f"KITE LOOK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
