#!/usr/bin/env python3
"""fix_rule_reward_risk.py - corrects only the Exness rule trades' reward:risk (the engine's 17.51 on
the first one, 4 Oct 2026), exactly as the fixed code writes it, and leaves every other byte alone."""
import glob
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fix_rule_reward_risk as fx
import trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


root = tempfile.mkdtemp()
d = os.path.join(root, "users", "0a1b2c3d4e5f6a7b")
os.makedirs(d)
path = os.path.join(d, "trades.csv")
base = dict(date="2026-10-04", index="BTC", option_type="CE", tracked_on="cfd", lot_size=1.0, lots=0.25, adx=33.5,
            score=-3, checks="gann- volume+ flowx wallsx spreadx daily_trend-", status="CLOSED — cleared manually")
rows = [
    # the first live rule trade, as it was logged: the engine's room-to-run on both rows
    dict(base, trade_id="BTC-20261004-034527", event="OPEN", time_ist="03:45:27", confidence="Rule", reward_risk=17.51,
         reach_points=2883.31, risk_points=254.97, status="OPEN", entry=84732.39, stop=84477.42, t3=84923.62),
    dict(base, trade_id="BTC-20261004-034527", event="CLOSE", time_ist="05:22:29", confidence="N/A", reward_risk=12.0,
         reach_points=2100.0, pnl=-4.05, entry=84732.39, stop=84477.42, t3=84923.62),
    # a rule trade that closed while the rule said buy again: its CLOSE row's reading was the rule's
    dict(base, trade_id="BTC-20261004-061500", event="OPEN", time_ist="06:15:01", confidence="Rule", reward_risk=9.1,
         reach_points=1500.0, entry=84000.0, stop=83700.0, t3=84225.0),
    dict(base, trade_id="BTC-20261004-061500", event="CLOSE", time_ist="06:45:01", confidence="Rule", reward_risk=8.0,
         reach_points=1400.0, entry=84000.0, stop=83700.0, t3=84225.0),
    # a trade of the RSI-2 rule that came after (target 1 x the stop), written before the fix: its own 1.0
    dict(base, trade_id="BTC-20261004-120000", event="OPEN", time_ist="12:00:01", confidence="Rule", reward_risk=5.5,
         reach_points=800.0, entry=84000.0, stop=83700.0, t3=84300.0),
    # an Exness trade from BEFORE the rules (the engine + 5R plan) - its reading WAS the engine's: untouched
    dict(base, trade_id="BTC-20261004-022054", event="OPEN", time_ist="02:20:54", confidence="High", reward_risk=3.2,
         reach_points=900.0, option_type="PE"),
    dict(base, trade_id="BTC-20261004-022054", event="CLOSE", time_ist="02:43:54", confidence="N/A", reward_risk=2.1,
         reach_points=600.0, option_type="PE"),
    # an AI trade on gold: its own plan's reward:risk - untouched
    dict(base, trade_id="GOLD-20261004-070000", event="OPEN", index="GOLD", time_ist="07:00:00", confidence="AI",
         reward_risk=1.5, reach_points=""),
]
for r in rows:
    trade_log._append(r, path)
other = os.path.join(root, "users", "9f8e7d6c5b4a3f2e", "trades.csv")
os.makedirs(os.path.dirname(other))
trade_log._append(dict(trade_id="NIFTY-20261001-101500", event="OPEN", index="NIFTY", option_type="CE", tracked_on="premium",
                       confidence="Rule", reward_risk=1.44, reach_points=120.0), other)   # an Indian row - never touched
os.chmod(path, 0o600)
before = open(path, "rb").read()
before_other = open(other, "rb").read()

print("1. WHAT IT WOULD CHANGE - AND NOTHING WRITTEN WITHOUT --apply")
ch = fx.plan_file(path)
check("the three rule trades' five rows, nothing else", [c[0] for c in ch] == [1, 2, 3, 4, 5], [c[0] for c in ch])
fx.main([root])
check("a dry run writes nothing", open(path, "rb").read() == before and not glob.glob(path + ".bak-rr-*"))
check("an Indian log has nothing to change", fx.plan_file(other) == [])

print("2. --apply: EXACTLY AS THE FIXED CODE WRITES IT")
fx.main([root, "--apply"])
got = {(r["trade_id"], r["event"]): r for r in trade_log._read_rows(path)}
a = got[("BTC-20261004-034527", "OPEN")]
check("the first rule trade's OPEN row: 0.75, no engine room-to-run", a["reward_risk"] == "0.75" and a["reach_points"] == "",
      (a["reward_risk"], a["reach_points"]))
b = got[("BTC-20261004-034527", "CLOSE")]
check("...its CLOSE row (the rule quiet at that moment): no reward:risk", b["reward_risk"] == "" and b["reach_points"] == "")
c = got[("BTC-20261004-061500", "CLOSE")]
check("a CLOSE row while the rule said buy again: the rule's own 0.75", c["reward_risk"] == "0.75" and c["reach_points"] == "")
d5 = got[("BTC-20261004-120000", "OPEN")]
check("each trade gets ITS OWN ratio from its own levels: the 1R RSI-2 trade 1.0, the candle rule's 0.75",
      d5["reward_risk"] == "1.0" and a["reward_risk"] == "0.75", (d5["reward_risk"], a["reward_risk"]))
check("the pre-rule Exness trade and the AI trade: untouched",
      got[("BTC-20261004-022054", "OPEN")]["reward_risk"] == "3.2" and got[("BTC-20261004-022054", "CLOSE")]["reward_risk"] == "2.1"
      and got[("GOLD-20261004-070000", "OPEN")]["reward_risk"] == "1.5")
check("every other column of the fixed rows unchanged", a["risk_points"] == "254.97" and a["checks"] == base["checks"]
      and b["pnl"] == "-4.05" and b["status"] == "CLOSED — cleared manually")
new = open(path, "rb").read().split(b"\r\n")
old = before.split(b"\r\n")
check("every other line byte for byte the same (and the same CRLF endings)", len(new) == len(old)
      and all(n == o for i, (n, o) in enumerate(zip(new, old)) if i not in (1, 2, 3, 4, 5)))
check("the Indian log untouched", open(other, "rb").read() == before_other)
baks = glob.glob(path + ".bak-rr-*")
check("a backup of the file as it was", len(baks) == 1 and open(baks[0], "rb").read() == before)
check("the file keeps its private 0600 mode", oct(os.stat(path).st_mode & 0o777) == "0o600", oct(os.stat(path).st_mode & 0o777))
check("run again: nothing left to correct", fx.plan_file(path) == [])
import config
# The BTC / gold entry rules were removed from the live config on 6 Oct 2026 (CFD_RULES = {}, the engine decides);
# the rule machinery stays for a rollback, so this test loads the retired rules to keep testing it.
config.CFD_RULES = dict(config.CFD_RULES_RETIRED)
was_rules = config.CFD_RULES
config.CFD_RULES = dict(was_rules, BTC=dict(was_rules["BTC"], target_r=2.0))
check("...even after the rule's settings change (they are never read: the rows keep their own ratio)",
      fx.plan_file(path) == [])
config.CFD_RULES = was_rules

print("3. A ROW APPENDED BETWEEN READING AND WRITING IS NEVER LOST")
open(path, "wb").write(before)
ch = fx.plan_file(path)
trade_log._append(dict(base, trade_id="BTC-20261004-090000", event="OPEN", confidence="Rule", reward_risk=0.75), path)
res = fx.apply_file(path, ch)
ids = [r["trade_id"] for r in trade_log._read_rows(path)]
check("a row added after the dry run: the fix re-reads the file, so it is kept", res.startswith("fixed")
      and ids[-1] == "BTC-20261004-090000" and len(ids) == len(rows) + 1, (res, len(ids)))
open(path, "wb").write(before)
ch = fx.plan_file(path)
real_copymode = fx.shutil.copymode
def copymode_then_a_ticket_closes(src, dst):       # runs after the new file is written, before it is swapped in
    real_copymode(src, dst)
    trade_log._append(dict(base, trade_id="BTC-20261004-091500", event="CLOSE", confidence="N/A"), path)
fx.shutil.copymode = copymode_then_a_ticket_closes
try:
    res = fx.apply_file(path, ch)
finally:
    fx.shutil.copymode = real_copymode
ids = [r["trade_id"] for r in trade_log._read_rows(path)]
check("a row appended WHILE it writes: the fix stands down, the row kept, no temp file left",
      "left alone" in res and ids[-1] == "BTC-20261004-091500" and not os.path.exists(path + ".fixrr"), res)

print()
if fails:
    print(f"FIX RULE REWARD RISK TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("FIX RULE REWARD RISK TEST PASSED")
