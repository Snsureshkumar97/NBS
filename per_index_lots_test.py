#!/usr/bin/env python3
"""Each index's own lots (the user, 7 Oct 2026: "make lot selects separate for markets each market i should select
different lots may you can add one in dashboard"). The ticket book keeps lots per instrument, saved across a restart,
a ticket opens with its own index's lots, the old one-selector request still sets them all, an unknown name changes
nothing, the AI desk follows them when it has no lots of its own, and the Dashboard's selectors (run in node) show and
save one index's lots."""
import os
import shutil
import subprocess
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. THE TICKET BOOK")
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
b = tickets.TicketBook(market="nse_index", path=path)
names = config.instruments_in("nse_index")
b.configure(lots=3)
check("the one selector of old (no index named): every index 3", all(b.lots_for(k) == 3 for k in names), {k: b.lots_for(k) for k in names})
b.configure(lots=2, lots_index="SENSEX")
b.configure(lots=1, lots_index="MIDCPNIFTY")
check("Sensex 2 and Midcap 1, the others still 3", (b.lots_for("SENSEX"), b.lots_for("MIDCPNIFTY"), b.lots_for("NIFTY"), b.lots_for("BANKNIFTY")) == (2, 1, 3, 3))
b.configure(lots=4.6, lots_index="NIFTY")
check("snapped to a size the market offers", b.lots_for("NIFTY") == 5, b.lots_for("NIFTY"))
b.configure(lots=9, lots_index="NOTANINDEX")
check("an unknown index name changes nothing", {k: b.lots_for(k) for k in names} == {"NIFTY": 5, "BANKNIFTY": 3, "SENSEX": 2, "MIDCPNIFTY": 1})
ses = b.session()
check("the session gives the page every index's lots", ses["lots_by"] == {"NIFTY": 5, "BANKNIFTY": 3, "SENSEX": 2, "MIDCPNIFTY": 1}, ses.get("lots_by"))
b2 = tickets.TicketBook(market="nse_index", path=path)
check("saved: a restart keeps each index's lots", {k: b2.lots_for(k) for k in names} == {"NIFTY": 5, "BANKNIFTY": 3, "SENSEX": 2, "MIDCPNIFTY": 1},
      {k: b2.lots_for(k) for k in names})
b2.configure(lots=2)
check("the old selector again: all indices 2 (its choices cleared)", all(b2.lots_for(k) == 2 for k in names) and not b2.lots_by)
src = open(os.path.join(HERE, "tickets.py")).read()
check("a ticket opens with ITS index's lots", '"lots": self.lots_for(book.name),' in src)

print("2. THE AI DESK AND THE API")
asrc = open(os.path.join(HERE, "ai_desk.py")).read()
check("the AI desk follows each index's lots when it has none of its own",
      'self.book.lots_by = dict(getattr(rules, "lots_by", {}) or {}) if self._lots is None else {}' in asrc)
check("...and its funds check uses that index's lots", "lots = self.book.lots_for(name)" in asrc)
wsrc = open(os.path.join(HERE, "web_server.py")).read()
check("the page's request names the index", 'lots_index=(form.get("lots_index") or "").upper() or None' in wsrc)

print("3. THE DASHBOARD'S SELECTORS")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False, "install node to run this section")
else:
    a = wsrc.index("// Each index's own lots (the user, 7 Oct 2026")
    z = wsrc.index("\n}\n", wsrc.index("function lotsDash(s){")) + 3
    prog = r'''
const assert = require("assert");
const esc = t => String(t);
let LAST = null, LOTS = 1, LOTS_HOLD = 0, CUR = "NIFTY", RENDERS = 0;
function render(){ RENDERS++; }
const posted = [];
let reply = null;
global.fetch = async (url, opt) => { posted.push(Object.fromEntries(opt.body)); return {json: async () => reply}; };
class Sel { constructor(k, v){ this.dataset = {k}; this.value = String(v); this.disabled = false; }
  closest(q){ return q.includes("data-sys") ? null : this; } }        // a Lots select, not a System one
const els = {};
const document = {activeElement: null};
function $(id){ return els[id] || null; }
''' + wsrc[a:z] + r'''
(async () => {
  els.kdlots = {innerHTML: "", dataset: {}, querySelectorAll(){ return SELS; }, onchange: null};
  let SELS = [];
  const s = {order: ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"], indices: {},
             session: {lots: 3, lots_by: {NIFTY: 3, BANKNIFTY: 3, SENSEX: 2, MIDCPNIFTY: 1}, lot_choices: [1, 2, 3, 4, 5]},
             live: {enabled: {NIFTY: true, BANKNIFTY: false, SENSEX: true}}};
  LAST = s;
  lotsDash(s);
  const h = els.kdlots.innerHTML;
  assert.ok(["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"].every(k => h.includes(`data-k="${k}"`)), "a selector per index");
  assert.ok(h.split('data-k="MIDCPNIFTY"')[1].includes("paper only") && !h.split('data-k="SENSEX"')[1].split("</label>")[0].includes("paper only"),
            "Midcap marked paper only - the live orders do not know it");
  SELS = ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"].map(k => new Sel(k, 1));
  lotsDash(s);
  assert.deepStrictEqual(SELS.map(x => x.value), ["3", "3", "2", "1"], "each shows its own index's lots");
  reply = {ok: true, session: {lots: 3, lots_by: {NIFTY: 3, BANKNIFTY: 3, SENSEX: 1, MIDCPNIFTY: 1}}};
  const sx = SELS[2]; sx.value = "1";
  els.kdlmsg = {textContent: ""};
  await els.kdlots.onchange({target: sx});
  assert.deepStrictEqual(posted[0], {lots: "1", lots_index: "SENSEX"}, "it saves ONE index's lots");
  assert.ok(sx.value === "1" && LAST.session.lots_by.SENSEX === 1 && els.kdlmsg.textContent.includes("SENSEX: the next ticket is 1 lot"),
            "the server's answer is shown and kept - " + els.kdlmsg.textContent);
  reply = {ok: false};
  const nf = SELS[0]; nf.value = "5";
  await els.kdlots.onchange({target: nf});
  assert.ok(nf.value === "3" && els.kdlmsg.textContent.includes("could not be saved"), "a refusal puts it back and says so");
  assert.strictEqual(lotsOf({lots: 4}, "NIFTY"), 4, "a server from before this: the market's one figure");
  console.log("ok:lots");
})().catch(e => { console.error(e); process.exit(1); });
'''
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    check("the Dashboard: a selector per index, Midcap marked paper only, one index saved at a time, a refusal put back",
          "ok:lots" in r.stdout and r.returncode == 0, out[-900:])

print()
print("PER INDEX LOTS TEST PASSED" if not fails else f"PER INDEX LOTS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
