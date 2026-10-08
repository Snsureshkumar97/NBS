#!/usr/bin/env python3
"""The index chart on TradingView's Lightweight Charts, and "Open in Kite" (the user, 8 Oct 2026: "instead of this chart
in signal section and chart section can we use directly zerodhas chart it is way more faster and easy to use").

Zerodha's chart cannot be shown inside another site (kite.zerodha.com: X-Frame-Options SAMEORIGIN), so the tool's chart
moved onto the same kind of engine - the open-source library the strike chart already used, served from vendor/ - and
Zerodha's own chart is a link. This runs the page's real chart code in node against a stand-in for the library that
records every call, and the server's link-building against stand-ins for the broker:

  1. the links: Kite's chart route for the index and for the open ticket's contract; none off Zerodha
  2. the provider: a contract's segment, symbol and token, picked the way option_token() always picked
  3. the chart: drawn in full once, then a tick moves only the newest candle; levels redrawn only when they change;
     a refresh keeps the view (on the newest candle, or on the same TIMES when scrolled back); price-only beside the
     Signal card, four panes on the Chart tab; the Kite buttons shown only when there is a link (the strike's only
     while a ticket is open)."""
import os
import shutil
import subprocess
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = open(os.path.join(HERE, "web_server.py")).read()

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

import web_server
import data_providers

print("1. THE LINKS")
check("the index: Kite's chart route, the symbol escaped",
      web_server.kite_chart_url("INDICES", "NIFTY 50", 256265) == "https://kite.zerodha.com/chart/ext/tvc/INDICES/NIFTY%2050/256265")
check("a contract: its own segment, symbol and token",
      web_server.kite_chart_url("NFO-OPT", "NIFTY26O1325000CE", "12345") == "https://kite.zerodha.com/chart/ext/tvc/NFO-OPT/NIFTY26O1325000CE/12345")


class Provider:                                   # a Kite provider: index tokens and the instrument list
    def index_token(self, key):
        return {"NIFTY": 256265}.get(key)
    def option_instrument(self, key, strike, side, expiry=None):
        return {"segment": "NFO-OPT", "tradingsymbol": f"NIFTY26O13{int(strike)}{side}", "token": 999}


class Tickets:
    def __init__(self, t): self.t = t
    def public(self, key): return {"ticket": self.t}


class Feed:
    def __init__(self, provider, ticket=None):
        self.p, self.tickets = provider, Tickets(ticket)
    def _provider(self): return (self.p, "ok", "")


links = web_server.Handler._kite_links
h = object.__new__(web_server.Handler)
out = links(h, Feed(Provider()), "NIFTY")
check("no open ticket: the index's link only", out == {"index": "https://kite.zerodha.com/chart/ext/tvc/INDICES/NIFTY%2050/256265"}, out)
out = links(h, Feed(Provider(), {"open": True, "strike": 25000, "option_type": "CE", "expiry": "2026-10-13"}), "NIFTY")
check("an open ticket: its own contract too", out.get("contract") == "https://kite.zerodha.com/chart/ext/tvc/NFO-OPT/NIFTY26O1325000CE/999"
      and out.get("contract_name") == "NIFTY26O1325000CE", out)
check("a closed ticket: no contract link", "contract" not in links(h, Feed(Provider(), {"open": False, "strike": 25000,
                                                                                         "option_type": "CE"}), "NIFTY"))
check("crypto (not on Zerodha): none", links(h, Feed(Provider()), "BTC") is None)
check("no Kite session (the free provider has no instrument list): none", links(h, Feed(object()), "NIFTY") is None)
check("no provider at all: none", links(h, Feed(None), "NIFTY") is None)

print("2. THE PROVIDER: A CONTRACT'S SEGMENT, SYMBOL AND TOKEN")
kp = object.__new__(data_providers.KiteDataProvider)
rows = [{"strike": 25000.0, "instrument_type": "CE", "expiry": "2026-10-20", "tradingsymbol": "NIFTY26O2025000CE",
         "instrument_token": 222, "segment": "NFO-OPT"},
        {"strike": 25000.0, "instrument_type": "CE", "expiry": "2026-10-13", "tradingsymbol": "NIFTY26O1325000CE",
         "instrument_token": 111, "segment": "NFO-OPT"},
        {"strike": 25000.0, "instrument_type": "PE", "expiry": "2026-10-13", "tradingsymbol": "NIFTY26O1325000PE",
         "instrument_token": 333, "segment": "NFO-OPT"}]
kp._all_option_instruments = lambda key: ("NFO", rows)
check("no expiry given: the nearest, as option_token() always picked",
      kp.option_instrument("NIFTY", 25000, "CE") == {"segment": "NFO-OPT", "tradingsymbol": "NIFTY26O1325000CE", "token": 111}
      and kp.option_token("NIFTY", 25000, "CE") == 111)
check("the ticket's own expiry when it is listed", kp.option_instrument("NIFTY", 25000, "CE", "2026-10-20")["token"] == 222
      and kp.option_token("NIFTY", 25000, "CE", "2026-10-20") == 222)
check("no such contract: None, from both", kp.option_instrument("NIFTY", 26000, "CE") is None
      and kp.option_token("NIFTY", 26000, "CE") is None)
rows.append({"strike": 26000.0, "instrument_type": "PE", "expiry": "2026-10-13", "tradingsymbol": "SENSEX26O1326000PE",
             "instrument_token": 444})
check("a row without a segment: the exchange's options segment", kp.option_instrument("NIFTY", 26000, "PE")["segment"] == "NFO-OPT")
kp._all_option_instruments = lambda key: (_ for _ in ()).throw(RuntimeError("Kite down"))
check("the instrument list failing: None, not an error, from both", kp.option_instrument("NIFTY", 25000, "CE") is None
      and kp.option_token("NIFTY", 25000, "CE") is None)

print("3. THE CHART, IN NODE, AGAINST A STAND-IN LIBRARY")
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
if not NODE:
    check("node is available", False, "install node to run this section")
else:
    def fn(name):
        a = SRC.index(f"function {name}(")
        return SRC[a:SRC.index("\n}\n", a) + 3]
    lines = SRC.splitlines()
    pick = lambda start: next(l for l in lines if l.startswith(start))
    page = "\n".join([pick("const esc=s=>"), pick("const num=(v,d=2)=>"), lines[lines.index(pick("const num=(v,d=2)=>")) + 1],
                      pick("const CH_VIEW = "), pick("const IST_S = "),
                      "const CH = {key:null, data:null, at:0, pinned:true};",
                      SRC[SRC.index("const LWC = {"):SRC.index("};", SRC.index("const LWC = {")) + 2]]
                     + [fn(n) for n in ("ccySym", "ccyLocale", "money", "openTicketFor", "ticketPnl", "onColour", "chBadge",
                                        "chBar", "chLine", "chLevels", "chWiden", "chColours", "chBuild", "chFill",
                                        "chLevelsApply", "chView", "chartZoom", "chartReset", "chKite", "chLegend",
                                        "chKeys", "chartDraw")])
    prog = r"""
const assert = require("assert");
let CCY = "INR", TAB = "chart", LAST = {tickets: {}};
const EL = {};
const $ = id => (EL[id] = EL[id] || {id, hidden: false, style: {}, textContent: "", innerHTML: "", href: null, title: "",
                                     removeAttribute(k){ this[k] = null; }});
const cv = {clientWidth: 1200};
const css = v => ({"--bg": "#fff", "--up": "#0a0", "--down": "#a00", "--warn": "#fa0", "--adx": "#aa0"})[v] || "#888";
const contractName = (i, k, o) => `${i} ${k} ${o}`;
const ocLib = () => Promise.reject(new Error("not in node"));
// the stand-in library: records what the chart is told
const LOG = [];
let RANGE = {from: 0, to: 0}, TIMES = null;
function Series(kind, pane){
  return {kind, pane, data: [], lines: [],
    setData(d){ this.data = d.slice(); LOG.push(["setData", kind]); },
    update(b){ const last = this.data[this.data.length - 1];
               if(last && b.time < last.time) throw new Error("older than the last bar");
               if(last && b.time === last.time) this.data[this.data.length - 1] = b; else this.data.push(b);
               LOG.push(["update", kind]); },
    createPriceLine(o){ const ln = {o}; this.lines.push(ln); LOG.push(["line+", kind, o.title]); return ln; },
    removePriceLine(ln){ this.lines = this.lines.filter(x => x !== ln); LOG.push(["line-", kind]); },
    applyOptions(){}};
}
const window = {LightweightCharts: {
  ColorType: {Solid: 0}, CrosshairMode: {Normal: 0}, LineStyle: {Solid: 0, Dotted: 1, Dashed: 2, SparseDotted: 4},
  CandlestickSeries: "candles", LineSeries: "line", HistogramSeries: "hist",
  createChart(el, o){
    LOG.push(["createChart"]);
    const series = [], panes = new Set([0]);
    const chart = {series,
      addSeries(kind, o, pane){ const s = Series(kind, pane || 0); s.o = o; series.push(s); panes.add(pane || 0); return s; },
      panes(){ return [...panes].map(() => ({setStretchFactor(){}})); },
      subscribeCrosshairMove(){}, applyOptions(){}, remove(){ LOG.push(["remove"]); },
      timeScale(){ return {
        getVisibleLogicalRange(){ return RANGE; },
        setVisibleLogicalRange(r){ RANGE = r; LOG.push(["view", r.from, r.to]); },
        getVisibleRange(){ return TIMES; },
        setVisibleRange(t){ LOG.push(["times", t.from, t.to]); },
        scrollToRealTime(){ LOG.push(["realtime"]); },
        subscribeVisibleLogicalRangeChange(){} }; }};
    return chart;
  }}};
const bars = n => Array.from({length: n}, (_, i) => [1790000000 + i * 900, 100 + i, 101 + i, 99 + i, 100.5 + i, null]);
const mk = n => ({index: "NIFTY", interval: "15m", candles: bars(n), ema_fast: Array(n).fill(1), ema_slow: Array(n).fill(null),
                  vwap: Array(n).fill(1), supertrend: Array(n).fill(1), rsi: Array(n).fill(50), macd_line: Array(n).fill(0),
                  macd_signal: Array(n).fill(0), macd_hist: Array(n).fill(0.5), adx: Array(n).fill(22), adx_gate: 20,
                  levels: {t1: 300, stop: 280}, room: {up_to: 320},
                  kite: {index: "https://kite.zerodha.com/chart/ext/tvc/INDICES/NIFTY%2050/256265",
                         contract: "https://kite.zerodha.com/chart/ext/tvc/NFO-OPT/X/1", contract_name: "X"}});
const count = (what, kind) => LOG.filter(e => e[0] === what && (kind == null || e[1] === kind)).length;
""" + page + r"""
// ---- the Chart tab: everything, in four panes
CH.key = "NIFTY"; CH.data = mk(300);
chartDraw();
const ch = LWC.chart;
assert.strictEqual(count("createChart"), 1);
assert.deepStrictEqual([...new Set(ch.series.map(s => s.pane))].sort(), [0, 1, 2, 3], "price, RSI, MACD, ADX");
assert.strictEqual(LWC.s.candles.data.length, 300);
assert.strictEqual(LWC.s.candles.data[0].time, 1790000000 + 19800, "the axis reads IST");
assert.ok(!("value" in LWC.s.slow.data[0]), "a warm-up null is a gap, not a zero");
const v = LOG.filter(e => e[0] === "view").pop();
assert.deepStrictEqual([v[1], v[2]], [300 - 130 - 0.5, 303], "the newest 130 candles in view");
assert.deepStrictEqual(LWC.lines.map(l => l.o.title), ["T1", "SL", "Room ↑"]);
assert.strictEqual(LWC.s.adx.lines.length, 1, "the ADX gate");
// ---- a tick: only the newest candle moves; levels are not redrawn
LOG.length = 0;
CH.data.candles[299][4] = 500; CH.data.candles[299][2] = 501;
chartDraw();
assert.strictEqual(count("setData"), 0, "no full redraw on a tick");
assert.strictEqual(count("update", "candles"), 1);
assert.strictEqual(LWC.s.candles.data[299].close, 500);
assert.strictEqual(count("line+") + count("line-"), 0, "levels unchanged: not redrawn");
// ...a new candle opens
CH.data.candles.push([CH.data.candles[299][0] + 900, 500, 502, 499, 501, null]);
chartDraw();
assert.strictEqual(LWC.s.candles.data.length, 301);
assert.strictEqual(LWC.n, 301);
// ---- a refresh while on the newest candle: stays on the newest
LOG.length = 0;
CH.pinned = true; CH.data = mk(305);
chartDraw();
assert.ok(count("setData", "candles") === 1 && count("realtime") === 1 && count("view") === 0, JSON.stringify(LOG));
// ---- a refresh while scrolled back: the same TIMES, not the same candle numbers
LOG.length = 0;
CH.pinned = false; TIMES = {from: 1790090000, to: 1790150000};
CH.data = mk(400);
chartDraw();
assert.deepStrictEqual(LOG.filter(e => e[0] === "times"), [["times", 1790090000, 1790150000]]);
assert.strictEqual(count("realtime"), 0);
// ---- a ticket opens: its levels, its badge, and the strike's Kite buttons
LOG.length = 0;
LAST.tickets.NIFTY = {ticket: {open: true, index: "NIFTY", strike: 25000, option_type: "CE", entry: 100, now: 110, pnl: 650,
                               index_targets: [310, 320, 330], index_stop: 290, entry_spot: 300}};
chartDraw();
assert.deepStrictEqual(LWC.lines.map(l => l.o.title), ["T1", "T2", "T3", "SL", "Entry", "Room ↑"]);
assert.ok(!EL.cvbadge.hidden && EL.cvbadge.textContent.startsWith("NIFTY 25000 CE · P&L +₹650"), EL.cvbadge.textContent);
assert.ok(!EL.cvkite.hidden && EL.cvkite.href.endsWith("/INDICES/NIFTY%2050/256265"));
assert.ok(!EL.cvkite2.hidden && !EL.tkite.hidden && EL.tkite.href.endsWith("/NFO-OPT/X/1"));
// ...and closes: the strike's buttons go, the index's stays
LAST.tickets.NIFTY = {ticket: null};
chartDraw();
assert.ok(EL.cvkite2.hidden && EL.tkite.hidden && !EL.cvkite.hidden && EL.cvbadge.hidden);
// ---- off Zerodha: no Kite buttons at all
CH.data = Object.assign(mk(50), {kite: null});
chartDraw();
assert.ok(EL.cvkite.hidden && EL.cvkite2.hidden && EL.tkite.hidden);
// ---- beside the Signal card: rebuilt price-only, the view kept
LOG.length = 0;
TAB = "signal"; RANGE = {from: 10, to: 40};
chartDraw();
assert.strictEqual(count("remove"), 1);
assert.deepStrictEqual([...new Set(LWC.chart.series.map(s => s.pane))], [0], "price only");
assert.ok(!LWC.s.rsi && !LWC.s.adx);
assert.deepStrictEqual(LOG.filter(e => e[0] === "view").pop(), ["view", 10, 40], "the view survives the rebuild");
assert.ok(!EL.cvkeys.innerHTML.includes("RSI") && EL.cvkeys.innerHTML.includes("Supertrend"));
// ---- no candles: the empty state
CH.data = null;
chartDraw();
assert.ok(EL.cvwait.hidden === false && EL.cvwait.textContent === "waiting for candles…");
// ---- the buttons zoom about the right edge
RANGE = {from: 100, to: 230};
chartZoom(1/1.3);
assert.deepStrictEqual([Math.round(RANGE.from), RANGE.to], [130, 230]);
console.log("ok:chart");
"""
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    check("drawn in full once; a tick moves only the newest candle; levels redrawn only when they change; a refresh keeps "
          "the view; price-only beside the Signal card; the Kite buttons only with a link",
          "ok:chart" in r.stdout and r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-1500:])

print("4. THE MARKUP")
check("the chart is a box the library draws in, with the badge and the empty state over it - no hand-drawn canvas",
      '<div id="cv" role="img"' in SRC and '<canvas id="cv"' not in SRC and 'id="cvbadge"' in SRC and 'id="cvwait"' in SRC)
check("the library is the one already served from vendor/, loaded the same way the strike chart loads it",
      "ocLib().then(() => chartDraw())" in SRC and 'el.src = "/vendor/lightweight-charts.js";' in SRC)
check("the Signal card's strike link sits beside its View chart button",
      '<a class="lbtn ocbtn kitebtn" id="tkite" target="_blank" rel="noopener noreferrer" hidden' in SRC)

print()
print("LW CHART TEST PASSED" if not fails else f"LW CHART TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
