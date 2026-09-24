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
      "grid-template-columns:360px minmax(0,1fr)" in KITE and ".wrap > .kcol{display:flex;flex-direction:column;gap:16px;grid-column:1;grid-row:1 / span 6;" in KITE
      and "position:sticky;top:64px" in KITE and '#markets::before{content:"Watchlist"' in KITE)
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
const document = {documentElement: {dataset: {look: @@LOOK@@}}, createElement: tag => new El(tag), querySelector: sel => sel === ".sidefoot" ? foot : null,
                  addEventListener: (ev, f) => { (docL[ev] = docL[ev] || []).push(f); }};
const winL = {};
const $ = id => id === "tabs" ? bar : id === "looksw" ? looksw : null;
const TAB_LABEL = {home: "Home"};
const addEventListener = (ev, f) => { (winL[ev] = winL[ev] || []).push(f); };
const matchMedia = q => ({matches: @@WIDE@@});
const api = new Function("document", "matchMedia", "$", "TAB_LABEL", "addEventListener", "bar", "groups",
  """ + __import__("json").dumps("") + r""" + %s + "\n" + %s + "\nreturn {navGroup, KITE_NAV};");
""" % (__import__("json").dumps(SRC[k0:k1]), __import__("json").dumps(SRC[n0:n1]))
    def run_case(look, wide):
        return prog.replace("@@LOOK@@", '"' + look + '"').replace("@@WIDE@@", "true" if wide else "false") + r"""
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


print("7. THE TWELVE UPGRADES (25 Sep 2026, 'do all'): the left column, the Signal page's order, the Dashboard")
check("the left column is a wrapper that is not there in the other looks, holding the watchlist and the day's panels",
      '<div class="kcol" id="kcol">' in SRC and '<div class="markets" id="markets" role="tablist"></div>\n  <div class="kside" id="kside"></div>' in SRC
      and ".kcol{display:contents}" in CSS and ".kside,.kdash{display:none}" in CSS)
check("(8) the strip under the top bar is gone on a wide screen - its state is in the left column", ':root[data-look="kite"] header{display:none}' in KITE)
sig_order = ["> .thead", "> .hero", "#tcontract", "#tissued", "#tlivestat", "#tstats", "#lswitch", "#ladder", "#laddernote", "#lnote", "#rr",
             "#reason", "#tovernight", "#twhy", "#tiles", "#gauges", "#room", "#checksbox", "#risk", "#gnote"]
orders = []
for sel in sig_order:
    m = re.search(r':root\[data-look="kite"\] ' + (r"#sigcard " if sel.startswith(">") else "") + re.escape(sel) + r"\{order:(\d+)", KITE)
    orders.append(int(m.group(1)) if m else None)
check("(2) the Signal card reads: the verdict and its trade, targets and stop, the risk and reward, why - then the tiles, the indicators, "
      "then the sizing (Capital) and the footnote last", None not in orders and orders == sorted(orders) and orders[0] == 1, orders)
check("(2) and the page: the signal first, the session and its counts after", ":root[data-look=\"kite\"] #sigcard{order:1}" in KITE
      and ":root[data-look=\"kite\"] #session{order:4}" in KITE and ":root[data-look=\"kite\"] #sfeed{order:5}" in KITE)
check("(3) the indicators are the left column of the card and the room to run the right one; the bars no longer span the page",
      "#sigcard{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:44px}" in KITE
      and "#gauges{order:16;grid-column:1;grid-row:span 2}" in KITE and "#room{order:17;grid-column:2}" in KITE and "#checksbox{order:18;grid-column:2}" in KITE)
check("(4) the live-orders control is a switch with its state in colour, on the card and in the left column",
      "#tlive::before,:root[data-look=\"kite\"] #ailive::before" in KITE and ".klive.on{color:#fff;background:#c62828" in KITE)
check("(5) the day's move and the trend's strength are said once (their own card beside the signal), the tile that repeated them is hidden",
      '.tile[data-k="day-move"],:root[data-look="kite"] #sigcard .tile[data-k="trend-strength"]{display:none}' in KITE
      and 'data-k="${esc(String(l).toLowerCase()' in SRC)
check("(6) the figures that matter are large and light", ".tile .v{font-size:26px;font-weight:400" in KITE and "#snet{font-size:30px" in KITE and ".kd-n{font-size:44px;font-weight:300" in KITE)
check("(7) the standing notice is Kite's pale yellow and one slim line; orange is kept for real warnings",
      ".notice.risk{background:#fff8e1;border:1px solid #f1dca0;color:#5f4b00;padding:7px 14px;font-size:13px}" in KITE
      and ".notice.stale{background:#fff4ef;" in KITE)
check("(9) the session's counts are figures with their labels beneath, and the numbers are marked up as such",
      "#sfeed > span b{display:block;font-size:22px" in KITE and "<span><b>${sess.issued}</b> ticket" in SRC and "<span><b>${sess.wins}</b> ran to target" in SRC)
check("(10) no data: one quiet line, the empty rows hidden - and the card says when it has none, and when it has some",
      '#sigcard[data-state="blank"] #bias{font-size:18px' in KITE and 'sc.dataset.state = "blank"' in SRC and 'sc.dataset.state = ""; }' in SRC)
check("(11) from 1500px the signal and the chart sit side by side, and the chart is told it has room",
      "@media (min-width:1500px){" in KITE and ".panes:has(.pane[data-pane=\"signal\"].on){display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);" in KITE
      and '> .pane[data-pane="chart"]{display:block;position:sticky;top:64px}' in KITE
      and 'if(name === "chart" || (name === "signal" && KITE_SPLIT())){ try{ chartDraw(); sparkline(); }catch(e){} }' in SRC
      and 'const KITE_SPLIT = () => LOOK_KITE && matchMedia("(min-width:1500px)").matches;' in SRC)
check("(12) the Dashboard leads with its two big figures and every index in a row; the Today recap and the side panels that repeat them step aside, "
      "and the wider markets come last",
      ".kdash{display:block;order:2;" in KITE and ".hsec:has(#htoday){display:none}" in KITE and ":has(.pane[data-pane=\"home\"].on) .kside :is(.kb-today,.kb-funds){display:none}" in KITE
      and ".hsec:has(#gmk){order:5}" in KITE and '<div class="kdash" id="kdash"></div>' in SRC)

if NODE:
    a0 = SRC.index("let KSIDE_HTML = \"\", KDASH_HTML = \"\";")
    a1 = SRC.index("function homeDraw(s){", a0)
    fn_src = SRC[a0:a1]
    prog = r"""
const assert = require("assert");
class Fake { constructor(){ this.style = {}; this.dataset = {}; this.attrs = {}; this.textContent = ""; this.className = ""; this.L = {}; this.clicks = 0; this.writes = 0; this._html = ""; }
  set innerHTML(v){ this._html = v; this.writes++; } get innerHTML(){ return this._html; }
  getAttribute(k){ return this.attrs[k] === undefined ? null : this.attrs[k]; } addEventListener(ev, f){ this.L[ev] = f; } click(){ this.clicks++; } }
function world(look){
  const els = {kside: new Fake(), kdash: new Fake(), beat: new Fake(), mkt: new Fake(), feed: new Fake(), upd: new Fake(), tlive: new Fake(), tclear: new Fake(), bias: new Fake()};
  els.beat.className = "beat live"; els.mkt.textContent = "Market open"; els.feed.textContent = "Live"; els.upd.textContent = "23:28:13 IST";
  els.tlive.style.display = ""; els.tlive.attrs["aria-pressed"] = "false"; els.tclear.style.display = "none"; els.bias.textContent = "No trade";
  const calls = {selected: [], tabs: []};
  const env = {LOOK_KITE: look === "kite", CUR: "BTC", $: id => els[id] || null,
    esc: t => String(t).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c])),
    money: v => (v >= 0 ? "+" : "−") + "$" + Math.abs(Math.round(v)).toLocaleString("en-US"),
    num: (v, d = 2) => v === null || v === undefined || isNaN(v) ? "—" : Number(v).toFixed(d),
    fundsLabel: f => f.asset + " " + f.available.toFixed(2),
    selectIndex: k => calls.selected.push(k), showTab: t => calls.tabs.push(t)};
  const f = new Function(...Object.keys(env), """ + __import__("json").dumps(fn_src) + r""" + "; return {kiteSide, kiteDash};")(...Object.values(env));
  return {els, calls, ...f};
}
const S = (over) => Object.assign({
  indices: {BTC: {bias: "NEUTRAL", spot: 84090.9, confidence: "N/A"}}, order: ["BTC"], tickets: {BTC: {ticket: null}},
  session: {net: 98, booked: 98, open: 0, issued: 10, wins: 0, stops: 2}, broker: {name: "Delta Exchange", connected: true, funds: {asset: "USD", available: 0.56}},
  market_label: "Bitcoin"}, over || {});
const TK = {status: "OPEN", strike: 84000, option_type: "CE", entry: 730, now: 786.5, pnl: 1412.5, stop: 610, targets: [860, 940, 1020], hit: {T1: true, T2: false, T3: false}};

// not the Zerodha look: nothing is written, nothing is wired
let w = world("terminal"); w.kiteSide(S()); w.kiteDash(S());
assert.strictEqual(w.els.kside.writes, 0); assert.strictEqual(w.els.kdash.writes, 0); assert.ok(!w.els.kside.L.click);

// a quiet day
w = world("kite"); w.kiteSide(S());
let h = w.els.kside.innerHTML;
assert.ok(h.includes("Market open") && h.includes("beat live") && h.includes("23:28:13 IST"), "the market's state, in the column");
assert.ok(h.includes("Live orders") && h.includes('aria-checked="false"') && h.includes(">OFF<") && !h.includes("klive on"), "the switch reads OFF");
assert.ok(h.includes("No open trade on BTC. No trade."), "no trade open");
assert.ok(h.includes("Today") && h.includes("+$98") && h.includes("10 tickets") && h.includes("2 stopped out"), "today");
assert.ok(h.includes("Funds") && h.includes("USD 0.56") && h.includes("available on Delta Exchange") && h.includes('href="/market"'), "funds and the way to the other market");
assert.ok(h.includes("kb-today") && h.includes("kb-funds"), "the panels the Dashboard hides are marked");

// an open trade, live orders on
w = world("kite"); w.els.tlive.attrs["aria-pressed"] = "true"; w.els.tclear.style.display = "";
w.kiteSide(S({tickets: {BTC: {ticket: TK}}}));
h = w.els.kside.innerHTML;
assert.ok(h.includes("Open trade &middot; BTC 84000 CE") && h.includes("+$1,413"), "the trade and its live result");
assert.ok(h.includes("730.00") && h.includes("786.50") && h.includes("610.00"), "entry, now and stop");
assert.ok(h.includes("T1 860.00 ✓") && h.includes("T2 940.00") && !h.includes("T2 940.00 ✓"), "targets, the one that has been hit ticked");
assert.ok(h.includes("Clear ticket"), "an exit when there is one to make");
assert.ok(h.includes('aria-checked="true"') && h.includes("klive on") && h.includes(">ON<"), "the switch reads ON");
assert.ok(!h.includes("No open trade"));
w.els.tclear.style.display = "none"; KS = null;
w.kiteSide(S({tickets: {BTC: {ticket: TK}}}));
assert.ok(!w.els.kside.innerHTML.includes("Clear ticket"), "no exit button when the page has none to press");

// a hidden live switch (the market cannot place real orders) leaves no switch
w = world("kite"); w.els.tlive.style.display = "none"; w.kiteSide(S());
assert.ok(!w.els.kside.innerHTML.includes("Live orders"));

// rewritten only when it changes
w = world("kite"); w.kiteSide(S()); w.kiteSide(S()); w.kiteSide(S());
assert.strictEqual(w.els.kside.writes, 1, "the same picture is not written again");
w.kiteSide(S({session: {net: 120, booked: 120, open: 0, issued: 11}}));
assert.strictEqual(w.els.kside.writes, 2, "a change is");

// the buttons press the real ones
w = world("kite"); w.kiteSide(S({tickets: {BTC: {ticket: TK}}}));
const press = k => w.els.kside.L.click({target: {closest: () => ({dataset: {kact: k}})}});
press("live"); press("clear"); press("clear");
assert.strictEqual(w.els.tlive.clicks, 1, "the switch presses the card's own switch (which asks you to confirm)");
assert.strictEqual(w.els.tclear.clicks, 2);
w.els.kside.L.click({target: {closest: () => null}});
assert.strictEqual(w.els.tlive.clicks, 1, "a click elsewhere in the column does nothing");

// nothing from outside is trusted as markup
w = world("kite"); w.els.bias.textContent = "<img src=x onerror=alert(1)>"; w.kiteSide(S());
assert.ok(!w.els.kside.innerHTML.includes("<img") && w.els.kside.innerHTML.includes("&lt;img"), "escaped");
w = world("kite"); w.kiteSide(S({broker: {name: "Zerodha", connected: false, connect_url: "/connect"}}));
assert.ok(w.els.kside.innerHTML.includes("Zerodha is not connected") && w.els.kside.innerHTML.includes('href="/connect"'), "a broker that is not connected says so and links to it");

// the Dashboard
w = world("kite");
const D = S({indices: {NIFTY: {bias: "BULLISH", spot: 24000, confidence: "High"}, BANKNIFTY: {bias: "BEARISH", spot: 55000, confidence: "N/A"}, SENSEX: {bias: "NEUTRAL", spot: 78000}},
             order: ["NIFTY", "BANKNIFTY", "SENSEX"],
             tickets: {NIFTY: {ticket: {status: "OPEN", strike: 24000, option_type: "CE", pnl: -450}}, BANKNIFTY: {ticket: null}, SENSEX: {}}});
w.kiteDash(D);
h = w.els.kdash.innerHTML;
assert.ok(h.includes("Today's result") && h.includes("+$98") && h.includes("booked +$98") && h.includes("Funds available") && h.includes("USD 0.56"), "the two big figures");
assert.strictEqual((h.match(/<tr data-k=/g) || []).length, 3, "a row per index");
const rowOf = k => (h.split('<tr data-k="' + k + '"')[1] || "").split("</tr>")[0];
assert.ok(rowOf("NIFTY").includes("Buy CE") && rowOf("NIFTY").includes("var(--up)"), "a bullish index reads Buy CE, in green");
assert.ok(rowOf("BANKNIFTY").includes("Buy PE") && rowOf("BANKNIFTY").includes("var(--down)"), "a bearish one reads Buy PE, in red");
assert.ok(rowOf("SENSEX").includes("No trade"), "a neutral one reads No trade");
assert.ok(h.includes("24000 CE") && h.includes("−$450"), "the open trade and its result");
assert.ok(h.includes(">High<") || h.includes("High"), "confidence, where there is one");
assert.strictEqual((h.match(/—<\/td>/g) || []).length >= 4, true, "an index with no trade shows dashes, not zeros");
w.els.kdash.L.click({target: {closest: () => ({dataset: {k: "BANKNIFTY"}})}});
assert.deepStrictEqual(w.calls.selected, ["BANKNIFTY"]); assert.deepStrictEqual(w.calls.tabs, ["signal"], "a row opens that index's Signal page");
w.kiteDash(D); assert.strictEqual(w.els.kdash.writes, 1, "not rewritten when nothing changed");
w = world("kite"); w.kiteDash(S({broker: {name: "Zerodha", connected: false}}));
assert.ok(w.els.kdash.innerHTML.includes("Zerodha is not connected"));
console.log("ok:panels");
"""
    prog = prog.replace("w.els.tclear.style.display = \"none\"; KS = null;", "w.els.tclear.style.display = \"none\";")
    import json as _json, subprocess as _sp
    r = _sp.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("the left column's and the Dashboard's own code, run for real: what they say, what they press, what they escape, and that they are silent outside the Zerodha look",
          "ok:panels" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-800:])

print("KITE LOOK TEST PASSED" if not fails else f"KITE LOOK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
