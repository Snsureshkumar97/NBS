#!/usr/bin/env python3
"""Live orders from the Trade settings tab: a dropdown per index, asking exactly what the Signal card's button asks.

The user, 20 Oct 2026: "in the trade setting tab only where it shows live order can we add add drop down over there to
turn on it will be easy". One function, setLive(index, on, ctl), now carries the questions and the request for both the
Signal card's button and the new dropdown - so the two can never ask different things before real orders. The page's
own setLive runs in node with the browser's confirm/fetch stood in for: nothing is sent anywhere.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


src = open(os.path.join(HERE, "web_server.py")).read()
lots = src[src.index("function lotsDash(s){"):src.index("\nfunction ", src.index("function lotsDash(s){") + 10)]

print("1. THE TRADE SETTINGS TABLE")
check("a dropdown in the Live orders column, only for an index that can place live orders",
      'select data-livesel="${esc(k)}"' in lots and "live && live.includes(k)" in lots
      and 'paper only</span>' in lots)
check("Off · paper / On · real orders", '<option value="off">Off &middot; paper</option>' in lots
      and '<option value="on">On &middot; real orders</option>' in lots)
check("its change goes through setLive, and a cancelled question puts it back",
      "await setLive(k, want, ls);" in lots and 'ls.value = now ? "on" : "off";' in lots)
check("every poll shows what is true now, but never while it is open or busy",
      "document.activeElement !== sel && !sel.disabled" in lots)
check("green when on - beating every look's plain select rule",
      ".kdlots .kdl-tab select.kdl-on{color:var(--up)" in src)
check("on a phone each index is a card with labelled rows, nothing wider than the screen",
      "@media(max-width:640px){\n  .kdlots .kdl-tab thead{display:none}" in src and 'data-th="Live orders"' in lots)
check("the note under it says live orders can be switched here",
      "Live orders can be switched here, on the Signal page (one index) or on Positions (all at once)" in src)

print("2. ONE SWITCH FOR SIGNAL AND TRADE SETTINGS")
a = src.index("async function setLive(index, on, ctl){")
z = src.index("// -------------------------------------------------- dashboard bulk actions")
fn = src[a:z]
check("the Signal card's button calls the same function", '$("tlive").onclick = () => {' in fn
      and 'return setLive(CUR, !(L.enabled || {})[CUR], $("tlive"));' in fn)
check("no second copy of the questions left behind", src.count("Place REAL orders on ${") == 1, src.count("Place REAL orders on ${"))
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False)
else:
    js = fn[:fn.index('$("tlive").onclick')] + r"""
const out = {};
let asked = [], answer = false, sent = [], rendered = 0, exness = [];
global.confirm = m => { asked.push(m.split("\n")[0]); return answer; };
global.alert = m => { out.alert = m; };
global.render = () => { rendered++; };
global.exnessLive = (...a) => { exness.push([a[0], a[1], a[3]]); };
global.fetch = async (u, o) => { sent.push([u, o.body.toString()]);
  return {json: async () => ({ok: true, live: {enabled: {NIFTY: o.body.get("on") === "1"}}})}; };
(async () => {
  global.LAST = {venue: "kite", live: {enabled: {NIFTY: false, SENSEX: true}}};
  const ctl = {disabled: false};
  answer = false; await setLive("NIFTY", true, ctl);
  out.cancel = {asked: asked.slice(), sent: sent.length, disabled: ctl.disabled};
  asked = []; answer = true; await setLive("NIFTY", true, ctl);
  out.ok = {asked: asked.slice(), sent: sent.slice(), live: LAST.live.enabled.NIFTY, rendered, disabled: ctl.disabled};
  asked = []; sent = []; await setLive("SENSEX", false, ctl);
  out.off = {asked: asked.slice(), sent: sent.slice()};
  LAST.venue = "exness"; asked = []; sent = [];
  await setLive("GOLD", true, ctl);
  out.exness = {exness, asked: asked.slice(), sent: sent.length};
  console.log(JSON.stringify(out));
})();
"""
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=60)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        o = {}
        print(r.stdout, r.stderr)
    c, k, f, x = o.get("cancel", {}), o.get("ok", {}), o.get("off", {}), o.get("exness", {})
    check("Cancel: asked 'Place REAL orders on NIFTY?', nothing sent", c.get("asked") == ["Place REAL orders on NIFTY?"]
          and c.get("sent") == 0 and c.get("disabled") is False, c)
    check("OK: one request, index NIFTY on, the page's state updated and redrawn",
          k.get("sent") == [["/api/live", "index=NIFTY&on=1"]] and k.get("live") is True and k.get("rendered") == 1
          and k.get("disabled") is False, k)
    check("switching off asks 'Stop placing live orders on SENSEX?' and sends on=0",
          f.get("asked") == ["Stop placing live orders on SENSEX?"] and f.get("sent") == [["/api/live", "index=SENSEX&on=0"]], f)
    check("on Exness: its own account pick and questions (exnessLive), nothing from here",
          x.get("exness") == [["GOLD", "rule", False]] and x.get("asked") == [] and x.get("sent") == 0, x)

print()
print("TRADESET LIVE TEST PASSED" if not fails else f"TRADESET LIVE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
