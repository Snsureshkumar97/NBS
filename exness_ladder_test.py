#!/usr/bin/env python3
"""The Signal page's target ladder on an Exness ticket (4 Oct 2026, the user: "beside the targets
it dont show the amount for t1" / "targets and stop loss", and no lots selector on Bitcoin).
Runs the page's OWN ladder() in Node against a fake DOM - skipped (not failed) without Node."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_server

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


NODE = shutil.which("node")
if not NODE:
    print("EXNESS LADDER TEST SKIPPED - no node on this machine")
    sys.exit(0)
scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", web_server.PAGE, re.S)
main = next(s for s in scripts if "function ladder(r, tk)" in s)
HARNESS = r'''
const els = {};
function mkEl(id){
  return {id, style: {}, dataset: {}, innerHTML: "", textContent: "", value: "", hidden: false, disabled: false,
    classList: {toggle(){}, add(){}, remove(){}, contains(){ return false; }}, setAttribute(){}, getAttribute(){ return null; },
    addEventListener(){}, removeEventListener(){}, appendChild(){}, querySelector(){ return mkEl("q"); },
    querySelectorAll(){ return []; }, closest(){ return null; }, after(){}, focus(){}, remove(){}, getContext(){ return null; },
    add(o){ (this.options = this.options || []).push(o); },
    getBoundingClientRect(){ return {width: 100, height: 100, top: 0, left: 0}; }};
}
globalThis.document = {getElementById: id => els[id] || (els[id] = mkEl(id)), querySelector: () => mkEl("q"),
  querySelectorAll: () => [], addEventListener(){}, createElement: () => mkEl("new"), body: mkEl("body"),
  documentElement: mkEl("html"), hidden: false, cookie: ""};
globalThis.window = globalThis; globalThis.location = {search: "", hash: "", pathname: "/app", href: "/app"};
globalThis.localStorage = {getItem(){ return null; }, setItem(){}}; globalThis.sessionStorage = globalThis.localStorage;
globalThis.navigator = {userAgent: "node"}; globalThis.matchMedia = () => ({matches: false, addEventListener(){}});
globalThis.fetch = () => new Promise(() => {}); globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
globalThis.requestAnimationFrame = () => 0; globalThis.getComputedStyle = () => ({getPropertyValue: () => ""});
globalThis.Option = function(t, v){ this.text = t; this.value = v; };
globalThis.ResizeObserver = function(){ this.observe = () => {}; }; globalThis.IntersectionObserver = globalThis.ResizeObserver;
globalThis.MutationObserver = globalThis.ResizeObserver; globalThis.addEventListener = () => {};
'''
TEST = r'''
;(function(){
  const rs = () => [...document.getElementById("ladder").innerHTML.matchAll(/class="rs"[^>]*>([^<]*)</g)].map(m => m[1]);
  const out = {};
  LAST = {market: "crypto", currency: "USD", session: {lot_choices: [0.01,0.02,0.05,0.1,0.25,0.5,1], lots: 0.25},
          broker: {accounts: []}, market_open: true};
  LOTS = 0.25; LOTS_SYNCED = true;
  ladder({index: "BTC", cfd: true, option_type: "PE", bias: "BEARISH", spot: 84000, lot_size: 1, cfd_spread: 10,
          targets: [83500, 83000, 82500], stop: 84300, exit_at: "T3", premium_targets: [null,null,null]}, null);
  out.btc_sell = rs();
  out.qty_btc = document.getElementById("lotqty").textContent;
  out.lots_shown = document.getElementById("lotswrap").style.display;
  out.choices = (document.getElementById("lots").options || []).map(o => o.value);
  out.levels = [...document.getElementById("ladder").innerHTML.matchAll(/class="n"[^>]*>([^<]*)</g)].map(m => m[1]);
  ladder({index: "GOLD", cfd: true, lot_size: 100}, {open: true, cfd: true, index: "GOLD", option_type: "CE", tracked_on: "index",
          entry: 4140.44, now: 4141, lot_size: 100, lots: 0.1, entry_spread: 0.26,
          targets: [4150.44, 4160.44, 4170.44], stop: 4134.44, exit_at: "T3", hit: {}, hit_time: {}});
  out.gold_ticket = rs();
  out.qty_gold = document.getElementById("lotqty").textContent;
  out.ticket_lswitch = document.getElementById("lswitch").style.display;
  out.ticket_lots = document.getElementById("lotswrap").style.display;
  out.ticket_btns = [document.getElementById("lb-index").style.display, document.getElementById("lb-premium").style.display];
  // NO signal on BTC (what the user saw: "i dont see lots") - the lots must still show
  document.getElementById("lots").options = [];
  document.getElementById("lots").dataset.sig = "";
  ladder({index: "BTC", cfd: true, bias: "NEUTRAL", spot: 84000, lot_size: 1, targets: [null, null, null], stop: null,
          premium_targets: [null, null, null]}, null);
  out.neutral_lswitch = document.getElementById("lswitch").style.display;
  out.neutral_lots = document.getElementById("lotswrap").style.display;
  out.neutral_choices = (document.getElementById("lots").options || []).map(o => o.value);
  out.neutral_btns = [document.getElementById("lb-index").style.display, document.getElementById("lb-premium").style.display];
  // ...and an Indian index with no signal keeps hiding the strip, as before
  LAST.session.lot_choices = [1,2,3,4,5]; LOTS = 1;
  ladder({index: "NIFTY", bias: "NEUTRAL", spot: 25000, lot_size: 65, targets: [null, null, null], stop: null,
          premium_targets: [null, null, null]}, null);
  out.nifty_neutral_lswitch = document.getElementById("lswitch").style.display;
  out.nifty_btns = [document.getElementById("lb-index").style.display, document.getElementById("lb-premium").style.display];
  // the Indian page's own session: whole lots, 1 chosen (the ladder now reads the server's lots first)
  LAST = {market: "nse_index", currency: "INR", session: {lot_choices: [1,2,3,4,5], lots: 1}, broker: {}, market_open: true};
  LOTS = 1;
  ladder({index: "NIFTY", option_type: "CE", ltp: 100, premium_targets: [120,140,160], premium_stop: 80,
          targets: [25100,25200,25300], stop: 24900, spot: 25000, lot_size: 65, premium_source: "live", exit_at: "T2"}, null);
  out.nifty = rs();
  out.qty_nifty = document.getElementById("lotqty").textContent;
  LOTS = 2; LAST.session.lots = 2; LAST.session.lot_choices = [1, 2, 3];
  ladder({index: "NIFTY", option_type: "CE", ltp: 100, premium_targets: [120,140,160], premium_stop: 80,
          targets: [25100,25200,25300], stop: 24900, spot: 25000, lot_size: 65, premium_source: "live", exit_at: "T2"}, null);
  out.qty_nifty2 = document.getElementById("lotqty").textContent;
  // The open ticket's Reward : risk cell. A rule's ticket froze its own (0.75); the reading has
  // since gone quiet (no reward:risk) - the cell keeps the ticket's. Any other ticket: the live one.
  const rrCell = () => (document.getElementById("tstats").innerHTML.match(/Reward : risk<\/div><div class="v"[^>]*>([^<]*)</) || [])[1];
  LAST = {market: "crypto", currency: "USD", session: {lot_choices: [0.01, 0.25], lots: 0.25}, broker: {accounts: []}, tickets: {}};
  const rtk = {open: true, cfd: true, index: "BTC", option_type: "CE", tracked_on: "index", entry: 84732.39, now: 84740,
               lot_size: 1, lots: 0.25, entry_spread: 18, reward_risk: 0.75, targets: [84796.13, 84859.88, 84923.62],
               stop: 84477.42, exit_at: "T3", hit: {}, hit_time: {}, entry_time: "03:45:27"};
  ticketBox({index: "BTC", cfd: true, bias: "NEUTRAL", spot: 84740, reach_to_risk: null}, {ticket: rtk, wait: null});
  out.rule_rr = rrCell();
  ticketBox({index: "BTC", cfd: true, bias: "NEUTRAL", spot: 84740, reach_to_risk: 17.51}, {ticket: rtk, wait: null});
  out.rule_rr_engine_left = rrCell();
  LAST = {market: "nse_index", currency: "INR", session: {lot_choices: [1], lots: 1}, broker: {}, tickets: {}};
  ticketBox({index: "NIFTY", bias: "BULLISH", spot: 25000, reach_to_risk: 1.44},
            {ticket: {open: true, index: "NIFTY", strike: 25000, option_type: "CE", tracked_on: "premium", entry: 100, now: 110,
                      lot_size: 65, lots: 1, targets: [120, 140, 160], stop: 80, exit_at: "T3", hit: {}, hit_time: {},
                      entry_time: "10:00:00", reward_risk: null}, wait: null});
  out.nifty_rr = rrCell();
  // The forward test's note under the rule's votes (the user, 4 Oct 2026: "run the forward test on demo with RSI-2")
  const ftRule = {label: "RSI-2 bounce", stop_atr: 3, target_r: 1,
                  forward_test: {since: "2026-10-04", trades_a_month: 30, win: "53-55%", per_trade: 55, luck_pct: 4.6}};
  const why = {votes: [{name: "RSI-2 extreme", vote: 1, reading: "buy"}, {name: "Stochastic", vote: 1, reading: "buy"}]};
  LAST.live = {guard: {BTC: {since: "2026-10-04T16:00:00+05:30", n: 3, wins: 2, win_pct: 66.7, pnl: -45.5, tripped: false,
                             why: null, min_trades: 20, expect_win: 0.87, max_dd_per_btc: 11037}}};
  gauges({index: "BTC", rule: ftRule}, why);
  out.ft_note = document.getElementById("gnote").textContent;
  out.ft_box = document.getElementById("ftbox").innerHTML;
  out.ft_box_shown = document.getElementById("ftbox").style.display;
  LAST.live.guard.BTC = {n: 25, wins: 16, win_pct: 64, pnl: -438, tripped: true,
                         why: "16 of 25 won (64%) - a rule that really wins 87% would do this badly less than 1% of the time",
                         min_trades: 20, expect_win: 0.87, max_dd_per_btc: 11037};
  gauges({index: "BTC", rule: ftRule}, why);
  out.ft_tripped = document.getElementById("ftbox").innerHTML;
  LAST.live = null;
  gauges({index: "GOLD", rule: {label: "Trend + momentum rule", stop_atr: 3, target_r: 0.75, forward_test: null}}, why);
  out.ft_gold_shown = document.getElementById("ftbox").style.display;
  out.rule_rows = document.getElementById("gauges").innerHTML;
  gauges({index: "NIFTY"}, why);
  out.engine_rows = document.getElementById("gauges").innerHTML;
  gauges({index: "GOLD", rule: {label: "Trend + momentum rule", stop_atr: 3, target_r: 0.75, forward_test: null}}, why);
  out.gold_note = document.getElementById("gnote").textContent;
  console.log(JSON.stringify(out));
})();
'''
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
    fh.write(HARNESS + main + TEST)
r = subprocess.run([NODE, fh.name], capture_output=True, text=True, timeout=60)
import json
try:
    out = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    print(r.stdout[-800:], r.stderr[-800:])
    out = {}
print("1. AN EXNESS SIGNAL: THE LOTS SELECTOR, AND DOLLARS BESIDE EVERY TARGET AND THE STOP")
check("the lots selector shows, with Exness's 0.01 - 1 lots", out.get("lots_shown") == "flex"
      and out.get("choices") == ["0.01", "0.02", "0.05", "0.1", "0.25", "0.5", "1"], out.get("choices"))
check("a SELL at 84,000, 0.25 lot, $10 spread: T1 83,500 = 500 x 0.25 - 2.50 = +$122.50 ... stop 84,300 = -$77.50",
      out.get("btc_sell") == ["+$122.50", "+$247.50", "+$372.50", "−$77.50"], out.get("btc_sell"))
check("prices to the cent", out.get("levels") == ["83,500.00", "83,000.00", "82,500.00", "84,300.00"], out.get("levels"))
print("2. AN OPEN EXNESS TICKET")
check("gold BUY 0.1 lot (10 oz), spread 0.26: T1 +$10/oz = +$97.40 ... stop -$62.60",
      out.get("gold_ticket") == ["+$97.40", "+$197.40", "+$297.40", "−$62.60"], out.get("gold_ticket"))
check("an open Exness ticket keeps the lots in reach (for the next ticket), without the option buttons",
      out.get("ticket_lswitch") == "flex" and out.get("ticket_lots") == "flex" and out.get("ticket_btns") == ["none", "none"],
      (out.get("ticket_lswitch"), out.get("ticket_lots"), out.get("ticket_btns")))
print("2b. NO SIGNAL ON BITCOIN - THE LOTS STILL SHOW (the user, 4 Oct 2026: \"i dont see lots\")")
check("the strip and the lots show, the choices are filled, the index/premium buttons are hidden",
      out.get("neutral_lswitch") == "flex" and out.get("neutral_lots") == "flex"
      and out.get("neutral_choices") == ["0.01", "0.02", "0.05", "0.1", "0.25", "0.5", "1"]
      and out.get("neutral_btns") == ["none", "none"],
      (out.get("neutral_lswitch"), out.get("neutral_lots"), out.get("neutral_choices"), out.get("neutral_btns")))
check("an Indian index with no signal still hides the strip, its buttons not hidden for good",
      out.get("nifty_neutral_lswitch") == "none" and out.get("nifty_btns") == ["", ""],
      (out.get("nifty_neutral_lswitch"), out.get("nifty_btns")))
print("3. THE INDIAN PREMIUM LADDER IS UNCHANGED")
check("NIFTY 1 lot (65): T1 120 vs 100 = +1,300 ... stop -1,300 (whole rupees, as before)",
      [x[-5:] for x in out.get("nifty") or []] == ["1,300", "2,600", "3,900", "1,300"]
      and (out.get("nifty") or [""])[0].startswith("+") and (out.get("nifty") or ["", "", "", ""])[3].startswith("−"),
      out.get("nifty"))
print("3b. THE OPEN TICKET'S REWARD : RISK (the user, 4 Oct 2026: \"fix the journal reward risk\" - it read 17.51)")
check("a rule's ticket shows its own 0.75 : 1 with the reading quiet", out.get("rule_rr") == "0.75 : 1", out.get("rule_rr"))
check("...never the engine's room-to-run, even if a reading still carried one", out.get("rule_rr_engine_left") == "0.75 : 1",
      out.get("rule_rr_engine_left"))
check("an Indian ticket still shows the live signal's, as before", out.get("nifty_rr") == "1.44 : 1", out.get("nifty_rr"))
print("3c. THE RSI-2 FORWARD TEST IS SAID PLAINLY ON THE CARD")
note = out.get("ft_note") or ""
check("the note says FORWARD TEST on the demo account since 2026-10-04 - not proven",
      "FORWARD TEST on the demo account since 2026-10-04 - not proven" in note, note[:160])
check("...with what the 3-year test expects, to hold the real results against",
      "about 30 trades a month, 53-55% won, about +$55 a trade per BTC" in note and "did as well 4.6% of the time" in note
      and "cannot prove it either way" in note, note)
check("the rule's rows use the wide gauge (room for '60 · sell above 90'); the engine's keep the narrow one",
      'class="gauge wide"' in (out.get("rule_rows") or "") and 'class="gauge"' in (out.get("engine_rows") or "")
      and 'class="gauge wide"' not in (out.get("engine_rows") or ""))
box = out.get("ft_box") or ""
check("the forward test has its OWN box near the top (the user, 4 Oct 2026: 'i dont see anything' - the note sat at the card's foot)",
      out.get("ft_box_shown") == "" and "Forward test · demo" in box and "since 2026-10-04 16:00 IST" in box
      and ">Trades</div><div class=\"v\">3<" in box and "Won (test 87%)" in box and ">66.7%<" in box
      and "−$45.50" in box and ">Armed<" in box
      and "after 20+ trades, the win rate is far below 87%, or the drop passes $11,037 per BTC" in box, box)
check("...the note under the votes no longer repeats it", "So far:" not in note)
check("...when it has tripped: Guard OFF, and why", ">OFF<" in (out.get("ft_tripped") or "")
      and "The guard switched live orders OFF: 16 of 25 won (64%)" in (out.get("ft_tripped") or ""))
check("...no box for a rule not on trial (gold)", out.get("ft_gold_shown") == "none", out.get("ft_gold_shown"))
check("a rule that is not on trial (gold) has no such note", "FORWARD TEST" not in (out.get("gold_note") or "")
      and (out.get("gold_note") or "").startswith("Trend + momentum rule:"), out.get("gold_note"))
print("3d. THE QUANTITY BESIDE THE LOTS (the user, 5 Oct 2026: 'can we add the qty also beside the lots')")
check("NIFTY 1 lot = 65 qty, 2 lots = 130 qty (what a live Zerodha order buys)",
      out.get("qty_nifty") == "= 65 qty" and out.get("qty_nifty2") == "= 130 qty", (out.get("qty_nifty"), out.get("qty_nifty2")))
check("Bitcoin 0.25 lot = 0.25 BTC; gold 0.25 lot = 25 oz", out.get("qty_btc") == "= 0.25 BTC" and out.get("qty_gold") == "= 25 oz",
      (out.get("qty_btc"), out.get("qty_gold")))
print("4. ON A PHONE THE MONEY SHOWS (the user, 4 Oct 2026: \"i dont see how much i get for targets\")")
css = web_server.PAGE
phone = css[css.index("@media(max-width:560px){\n  .rung{gap:8px}"):]
phone = phone[:phone.index("\n}") + 2]
check("the phone rule no longer hides the money column", ".rung .rs{display:none}" not in css
      and ".rung .rs{width:auto;min-width:70px" in phone, phone[:200])
check("...it drops the progress bar instead", ".rung .bar{display:none}" in phone)
print()
if fails:
    print(f"EXNESS LADDER TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("EXNESS LADDER TEST PASSED")
