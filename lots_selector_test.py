#!/usr/bin/env python3
"""The Signal page's lot selector shows the size the SERVER has, and says so
when a change did not take.

On 23 Sep 2026 every ticket and real order went out at one lot under a
selector that showed more: the page synced the size from the server once per
load, so a restart that reset the server left it showing the old number, and a
failed change was swallowed by an empty .catch. The AI tab's selector never had
this - it is rebuilt from the server's figure on every refresh and alerts on
failure - and now the Signal page does the same. The page's own ladder() and
its own change handler are run for real in node, against a fake fetch.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available to run the page's own functions", False, "install node to run this test")
    print("LOTS SELECTOR TEST FAILED")
    sys.exit(1)

SRC = open(os.path.join(HERE, "web_server.py")).read()
h = SRC.index("let CCY = ")
i = SRC.index("function feedTag(")
a = SRC.index("const num=")
b = SRC.index("function unitLabel(")
c = SRC.index("function ladder(r, tk){")
d = SRC.index("function riskBox(")
ha = SRC.index('$("lots").onchange = async e => {')
hb = SRC.index('$("tclear").onclick')

prog = """
const assert = require("assert");
const EL = {};
function mkEl(id){
  return {id, innerHTML: "", textContent: "", title: "", value: "", disabled: false,
          dataset: {}, options: [], style: {}, classList: {toggle(){}},
          add(opt){ this.options.push(opt); }};
}
function $(id){ return EL[id] || (EL[id] = mkEl(id)); }
class Option { constructor(text, value){ this.text = text; this.value = value; } }
let LAST = null, CUR = "NIFTY", RENDERS = 0;
const ALERTS = [], FETCHES = [];
let FETCH_NEXT = null;
function alert(m){ ALERTS.push(m); }
async function fetch(url, opts){ FETCHES.push({url, method: opts.method, body: String(opts.body)}); return FETCH_NEXT(); }
""" + SRC[h:i] + SRC[a:b] + SRC[b:c] + SRC[c:d] + r'''
const REC = {ltp: 130.0, premium_targets: [145.0, 154.0, 160.0], premium_stop: 110.0, strike: 25000,
             option_type: "CE", exit_at: "T2", lot_size: 65, premium_source: "live", targets: [], odds: {}};
function render(d){ RENDERS++; ladder(REC, {open: false}); }
''' + SRC[ha:hb] + r'''
const CH = [1, 2, 3, 4, 5];
const poll = lots => { LAST = {market_open: true, session: {lots, lot_choices: CH}}; render(LAST); };
const ok = lots => async () => ({ok: true, json: async () => ({ok: true, session: {lots, lot_choices: CH}})});
const sel = () => $("lots");
const reset = () => { ALERTS.length = 0; FETCHES.length = 0; LOTS_HOLD = 0; sel().disabled = false; };

(async () => {
  // ---- what the page SHOWS: the server's figure, on every reading --------
  poll(3);
  assert.strictEqual(LOTS, 3, "a fresh page takes the server's size");
  assert.strictEqual(sel().value, "3", "...and shows it");

  poll(1);                                    // the server restarted and forgot: it is back at 1 lot
  assert.strictEqual(LOTS, 1, "a later reading that differs is adopted, not ignored - the exact 23 Sep failure");
  assert.strictEqual(sel().value, "1", "...and the selector no longer promises the old 3");

  LOTS = 4; LOTS_HOLD = Date.now() + 5000;    // a change made here a moment ago
  poll(1);                                    // a poll that was already on its way, still carrying the old size
  assert.strictEqual(LOTS, 4, "a change made a moment ago is not flipped back by an older poll");
  assert.strictEqual(sel().value, "4");
  LOTS_HOLD = 0;
  poll(1);
  assert.strictEqual(LOTS, 1, "once the hold has passed the server's figure wins again");
  console.log("ok:shows");

  // ---- a change the server takes ---------------------------------------
  reset(); poll(1);
  FETCH_NEXT = ok(4); sel().value = "4";
  await $("lots").onchange({target: sel()});
  assert.strictEqual(FETCHES.length, 1);
  assert.strictEqual(FETCHES[0].url, "/api/ticket");
  assert.strictEqual(FETCHES[0].method, "POST");
  assert.strictEqual(FETCHES[0].body, "lots=4", "the size is sent to the server");
  assert.strictEqual(LOTS, 4);
  assert.strictEqual(LAST.session.lots, 4, "the page's copy of the server's session is updated from the reply");
  assert.strictEqual(sel().disabled, false, "the selector is usable again");
  assert.strictEqual(ALERTS.length, 0, "and nothing is alerted");
  poll(4);
  assert.strictEqual(LOTS, 4, "and the next reading agrees");
  console.log("ok:takes");

  // ---- the server settles on a different size than asked ------------------
  reset(); poll(1);
  FETCH_NEXT = ok(5); sel().value = "3";
  await $("lots").onchange({target: sel()});
  assert.strictEqual(LOTS, 5, "the size the server snapped to is what stays, not what was asked for");
  assert.strictEqual(sel().value, "5");
  console.log("ok:snaps");

  // ---- a change that does NOT take: put back, and said out loud ----------
  const failures = {
    "the server refuses it": async () => ({ok: true, json: async () => ({ok: false, message: "no"})}),
    "the request fails": async () => { throw new Error("network down"); },
    "the reply is not JSON (signed out: the login page)": async () => ({ok: true, json: async () => { throw new SyntaxError("<html>"); }}),
    "the reply carries no session": async () => ({ok: true, json: async () => ({ok: true})}),
    "the server says not ok even with a session attached": async () => ({ok: true, json: async () => ({ok: false, session: {lots: 4, lot_choices: CH}})}),
  };
  for(const [why, fn] of Object.entries(failures)){
    reset(); poll(2);
    FETCH_NEXT = fn; sel().value = "4";
    await $("lots").onchange({target: sel()});
    assert.strictEqual(LOTS, 2, why + ": the size goes back to what the server has");
    assert.strictEqual(sel().value, "2", why + ": and so does the selector");
    assert.strictEqual(ALERTS.length, 1, why + ": and it is alerted, not swallowed");
    assert.ok(ALERTS[0].includes("could not be saved") && ALERTS[0].includes("still 2"), why + ": in words - " + ALERTS[0]);
    assert.strictEqual(LOTS_HOLD, 0, why + ": and the server's figure is trusted at once again");
    assert.strictEqual(sel().disabled, false, why + ": and the selector is usable again");
  }
  // A reading that carries no lot size (a partial poll): there is nothing for the sync to put it back to,
  // so the page must fall back to what it showed before the change rather than keep the size that failed.
  reset(); LAST = {market_open: true, session: {lot_choices: CH}}; LOTS = 2; LOTS_SYNCED = true;
  FETCH_NEXT = async () => { throw new Error("network down"); }; sel().value = "4";
  await $("lots").onchange({target: sel()});
  assert.strictEqual(LOTS, 2, "with no server figure to fall back on, it goes back to what the page showed before");
  assert.strictEqual(ALERTS.length, 1);
  console.log("ok:fails");

  // ---- while a change is in flight ---------------------------------------
  reset(); poll(1);
  let release;
  FETCH_NEXT = () => new Promise(res => { release = () => res({ok: true, json: async () => ({ok: true, session: {lots: 4, lot_choices: CH}})}); });
  sel().value = "4";
  const pending = $("lots").onchange({target: sel()});
  assert.strictEqual(sel().disabled, true, "the selector is locked while the server decides, so two changes cannot cross");
  assert.strictEqual(LOTS, 4, "the new size shows at once");
  poll(1);                                    // an old poll lands mid-flight
  assert.strictEqual(LOTS, 4, "and an older reading landing mid-flight does not flip it back");
  release();
  await pending;
  assert.strictEqual(sel().disabled, false);
  assert.strictEqual(LOTS, 4);
  console.log("ok:inflight");
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
'''

r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
out = (r.stdout or "") + (r.stderr or "")
check("the selector shows the server's size on every reading, holding only a change made a moment ago",
      "ok:shows" in r.stdout, out[-1200:])
check("a change the server takes is sent, confirmed from its reply, and stays", "ok:takes" in r.stdout, out[-1200:])
check("a size the server snaps to a market choice is the one that stays", "ok:snaps" in r.stdout, out[-1200:])
check("a change that does not take - refused, failed, signed out - is put back and alerted, never swallowed",
      "ok:fails" in r.stdout, out[-1200:])
check("the selector is locked while a change is in flight and an old reading cannot flip it",
      "ok:inflight" in r.stdout, out[-1200:])
check("the run exits clean", r.returncode == 0, out[-600:])

handler = SRC[ha:hb]
check("the handler has no empty catch left to swallow a failed change", ".catch(()=>{})" not in handler and "alert(" in handler)

print("LOTS SELECTOR TEST PASSED" if not fails else f"LOTS SELECTOR TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
