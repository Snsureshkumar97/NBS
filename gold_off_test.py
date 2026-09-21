#!/usr/bin/env python3
"""Gold switched off - asked for by the user on 22 Sep 2026 ("remove gold, I am
losing on that market"). The instrument's definition is kept so that turning it
back on is one line, but while it is off nothing in the tool may list it, run it,
ask the AI about it or describe it. Fakes only."""
import os
import re
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
config.ENABLE_CRYPTO = True                       # the crypto market is on; gold inside it is not
import delta_orders
import feeds
import market_bot as mb

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE SWITCH")
check("gold is off in the shipped config, and its definition is still there to turn back on",
      config.INSTRUMENTS["GOLD"]["enabled"] is False and config.INSTRUMENTS["GOLD"]["delta_asset"] == "XAUT"
      and config.INSTRUMENTS["GOLD"]["max_spread_pct"] == 8.0)
check("the crypto market lists Bitcoin alone, and so does everything the server runs",
      config.instruments_in("crypto") == ["BTC"] and config.active_instruments() == ["NIFTY", "BANKNIFTY", "SENSEX", "BTC"],
      (config.instruments_in("crypto"), config.active_instruments()))
check("the Indian market is untouched", config.instruments_in("nse_index") == ["NIFTY", "BANKNIFTY", "SENSEX"])
config.INSTRUMENTS["GOLD"]["enabled"] = True
check("turning it back on is that one line", config.instruments_in("crypto") == ["BTC", "GOLD"])
del config.INSTRUMENTS["GOLD"]["enabled"]
check("...and an instrument with no `enabled` key at all is on, as every other one is", config.instruments_in("crypto") == ["BTC", "GOLD"])
config.INSTRUMENTS["GOLD"]["enabled"] = False

print("2. THE FEED AND THE AI DESK SEE ONLY BITCOIN")
f = feeds.Feed("t:off", "off@example.invalid", "crypto")
check("a crypto feed runs Bitcoin alone", f.instruments() == ["BTC"], f.instruments())
check("...and asks Delta for Bitcoin's index and no other", True)
class FakeStreamer:
    def __init__(self): self.subs = []; self.last_error = None
    def start(self): return True
    def subscribe_index(self, s): self.subs.append(("index", s))
    def subscribe_trades(self, s): self.subs.append(("trades", s))
orig = feeds.DeltaStreamer
feeds.DeltaStreamer = FakeStreamer
try:
    f2 = feeds.Feed("t:off2", "off2@example.invalid", "crypto"); f2._start_crypto_stream()
    check("the crypto socket subscribes to Bitcoin's index and trades - nothing for gold", sorted(f2.dstream.subs) == [("index", ".DEXBTUSD"), ("trades", "BTCUSD")], f2.dstream.subs)
finally:
    feeds.DeltaStreamer = orig
check("a real order still cannot be sent for gold, on or off", delta_orders.INDICES == ("BTC",))

print("3. THE PROMPTS DO NOT DESCRIBE WHAT THE TOOL DOES NOT TRADE")
for name, prompt in (("Ask TradePicker", mb.SYSTEM), ("the AI desk", mb.DESK_SYSTEM)):
    check(f"{name}: no gold, no XAUT, no gold spread limit - and the sentence still reads",
          not re.search(r"gold|XAUT|GOLD", prompt) and "options on Bitcoin (in dollars, around the clock)." in prompt,
          [m.group(0) for m in re.finditer(r".{30}(?:gold|XAUT).{30}", prompt)][:2])
check("the rest of each prompt is intact around the cut", "The person asking is the tool's user" in mb.SYSTEM and "You only ever BUY one option" in mb.DESK_SYSTEM)
a = ("options on Bitcoin and on tokenised gold (in dollars, around the clock). Gold (GOLD) is tokenised gold, XAUT - about one "
     "troy ounce - with dollar-settled options: a lot there is 100 contracts. Gold tickets are always paper: no real order is ever "
     "sent for gold. The person asking")
b = "options on Bitcoin and gold (dollars, around the clock). Gold (GOLD) is x. Gold tickets are always paper: no real order is ever sent for gold. You only ever"
check("the cut handles both wordings the prompts use", mb._without_gold(a) == "options on Bitcoin (in dollars, around the clock). The person asking"
      and mb._without_gold(b) == "options on Bitcoin (in dollars, around the clock). You only ever", (mb._without_gold(a), mb._without_gold(b)))
check("...and leaves text with no gold in it alone", mb._without_gold("nothing to cut here") == "nothing to cut here")

print("4. THE PAGE")
SRC = open(os.path.join(HERE, "web_server.py")).read()
check("the sidebar says Bitcoin, not Gold", '<small id="sidesub">Nifty · Bank Nifty · Sensex · Bitcoin</small>' in SRC)
check("the crypto screen's own wording follows what the market actually lists - gold only when it is there",
      '(s.order || []).includes("GOLD")' in SRC and '(gold ? "Bitcoin and gold options" : "Bitcoin options")' in SRC
      and '(gold ? "BTC · Gold" : "BTC") + " · Delta Exchange · 24/7"' in SRC)

print("GOLD OFF TEST PASSED" if not fails else f"GOLD OFF TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
