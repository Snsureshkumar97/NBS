#!/usr/bin/env python3
"""vm_precheck.py is the only thing standing between a routine deploy and
restarting the server mid-trade. This is a rewrite (see the module's own
docstring for why the original is gone) - a bug here either blocks every
restart forever (safe but useless) or, far worse, waves one through while a
real position is open. Fakes only - a temporary trade-log directory; nothing
here reaches the real log folder or any broker.

Each scenario gets its OWN fresh TRADING_TOOL_HOME before check() runs -
check() scans the WHOLE log tree, so two scenarios sharing one tree would
contaminate each other's verdict (an account from an earlier scenario left
blocking would make every later scenario look blocked too, regardless of
what it actually set up). The per-account functions (real_positions,
open_paper_trades) don't have this problem and are used directly wherever a
check does not need the full-tree verdict.
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import trade_log
import vm_precheck as vp

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def fresh_home():
    os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()


def account(key):
    d = os.path.join(trade_log.log_dir(), "users", key)
    os.makedirs(d, exist_ok=True)
    return d


def write_trades(d, rows):
    path = os.path.join(d, trade_log.CSV_NAME)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trade_log.FIELDS)
        w.writeheader()
        for r in rows:
            full = {k: "" for k in trade_log.FIELDS}
            full.update(r)
            w.writerow(full)
    return path


def write_sidecar(d, suffix, positions):
    path = os.path.join(d, trade_log.CSV_NAME + suffix)
    with open(path, "w") as f:
        json.dump({"enabled": {}, "enabled_ai": {}, "positions": positions}, f)
    return path


def op(tid, event, index="NIFTY", strike="23000", option_type="CE",
       date=None, time_ist="10:00:00"):
    # The default is TODAY, computed fresh - a hard-coded date only reads as
    # "today" for however long it takes the calendar to move past it, and
    # section 5 below (which checks for the literal word "today") silently
    # broke the day after this file was first written for exactly that
    # reason - the same staleness this whole feature exists to catch live.
    if date is None:
        date = vp.now_ist().strftime("%Y-%m-%d")
    return {"trade_id": tid, "event": event, "date": date, "time_ist": time_ist,
            "index": index, "strike": strike, "option_type": option_type}


print("1. NOTHING ANYWHERE: CLEAR, NOTHING TO SAY BUT THE VERDICT")
fresh_home()
ok, lines = vp.check()
check("ok with an empty log dir", ok is True)
check("the only line is the verdict", lines == ["CLEAR TO RESTART"], lines)

print("2. A REAL, OPEN POSITION BLOCKS - THIS IS THE ONE CASE THAT MUST NEVER BE MISSED")
fresh_home()
d = account("acct1")
write_trades(d, [op("BTC-1", "OPEN", index="BTC", strike="80000", option_type="CE")])
write_sidecar(d, ".delta.json", {"BTC-1": {"state": "open", "index": "BTC"}})
ok, lines = vp.check()
check("blocked", ok is False)
check("names the account, the sidecar file and the trade id, as a list",
      any("REAL POSITION - BLOCKS: acct1 " in l and "trades.csv.delta.json" in l and "BTC-1" in l for l in lines), lines)
check("the verdict line is still last and says BLOCKED", lines[-1] == "BLOCKED - not restarting")
check("a real position is NOT also listed as a paper 'does not block' line - it would understate what it is",
      not any("does not block" in l and "BTC-1" in l for l in lines))

print("3. EVERY NON-'closed' STATE THESE ORDER FILES ACTUALLY USE BLOCKS, NOT JUST 'open'")
for state in ("placing", "entering", "failed", "attention", "exiting", "open", "some-future-state-this-has-never-seen"):
    fresh_home()
    d = account("acct")
    write_sidecar(d, ".live.json", {"T-1": {"state": state}})
    ok, _ = vp.check()
    check(f"state={state!r} blocks (fail-safe: only 'closed' is trusted)", ok is False, state)

print("4. 'closed' IS THE ONLY STATE THAT DOES NOT BLOCK")
fresh_home()
d = account("closed_one")
write_sidecar(d, ".delta.json", {"T-2": {"state": "closed"}})
ok, lines = vp.check()
check("a closed real position does not block", ok is True, lines)

print("5. PAPER TRADES NEVER BLOCK, AND SAY SO - INDIAN INDICES LABELLED nse_index")
fresh_home()
d = account("paper_acct")
write_trades(d, [op("P-1", "OPEN", index="NIFTY", strike="23500", option_type="PE", time_ist="09:30:15")])
ok, lines = vp.check()
check("still clear - no real position at all for this account", ok is True)
check("the paper line names the account, index, strike, side, time and market",
      any(l.startswith("open today: paper_") and "NIFTY 23500 PE" in l and "09:30:15" in l
          and "nse_index paper (does not block)" in l for l in lines), lines)

print("6. A CRYPTO PAPER TRADE IS LABELLED crypto, NOT nse_index")
fresh_home()
d = account("crypto_paper")
write_trades(d, [op("C-1", "OPEN", index="BTC", strike="82000", option_type="CE")])
ok, lines = vp.check()
check("labelled crypto", ok is True and any("crypto paper (does not block)" in l for l in lines), lines)

print("7. A CLOSED PAPER TRADE (OPEN+CLOSE BOTH PRESENT) IS NOT REPORTED AT ALL")
fresh_home()
d = account("closed_paper")
write_trades(d, [op("CP-1", "OPEN"), op("CP-1", "CLOSE")])
ok, lines = vp.check()
check("nothing said about it - it is neither open nor blocking", ok is True and not any("CP-1" in l for l in lines), lines)

print("8. A TRADE THAT IS BOTH IN trades.csv AND THE REAL SIDECAR IS ONLY EVER COUNTED AS REAL, NEVER TWICE")
fresh_home()
d = account("dual_listed")
write_trades(d, [op("D-1", "OPEN", index="BTC", strike="81000", option_type="PE")])
write_sidecar(d, ".delta.json", {"D-1": {"state": "entering"}})
ok, lines = vp.check()
check("blocked once, for the real reason", ok is False and sum("D-1" in l for l in lines) == 1, lines)
check("never also shown as a non-blocking paper line", not any("does not block" in l and "D-1" in l for l in lines))

print("9. A SIDECAR FILE THAT EXISTS BUT IS CORRUPT BLOCKS - UNREADABLE IS NOT THE SAME AS ABSENT")
fresh_home()
d = account("corrupt")
path = os.path.join(d, trade_log.CSV_NAME + ".live.json")
with open(path, "w") as f:
    f.write("{not valid json")
ok, lines = vp.check()
check("blocks rather than silently skipping a file it could not read", ok is False, lines)
check("says so plainly", any("unreadable" in l.lower() for l in lines), lines)

print("10. A MISSING SIDECAR FILE IS NORMAL, NOT A PROBLEM - MOST ACCOUNTS HAVE NEVER USED LIVE ORDERS")
fresh_home()
d = account("never_live")
write_trades(d, [op("NL-1", "OPEN", index="BTC"), op("NL-1", "CLOSE")])
ok, lines = vp.check()
check("no sidecar file at all is simply ignored, not flagged", ok is True, lines)

print("11. 'TODAY' VS AN OLDER DATE IS LABELLED HONESTLY")
fresh_home()
d = account("old_paper")
write_trades(d, [op("OLD-1", "OPEN", index="BTC", date="2020-01-01", time_ist="03:00:00")])
ok, lines = vp.check()
check("a trade opened on a real, different date says that date, not 'today'",
      ok is True and any("open 2020-01-01:" in l for l in lines), lines)
fresh_home()
d = account("today_paper")
write_trades(d, [op("TD-1", "OPEN", index="BTC", date=vp.now_ist().strftime("%Y-%m-%d"))])
ok, lines = vp.check()
check("...and a trade opened today genuinely says 'today'", ok is True and any("open today:" in l for l in lines), lines)

print("12. account_dirs() IS STABLE AND SORTED, SO REPEATED RUNS READ THE SAME")
fresh_home()
account("zzz"); account("aaa"); account("mmm")
dirs1 = vp.account_dirs()
dirs2 = vp.account_dirs()
check("same list twice in a row, in sorted order", dirs1 == dirs2 and dirs1 == sorted(dirs1), dirs1)

print("13. THE EXIT CODE MATCHES THE VERDICT - A CALLER THAT ONLY CHECKS $? MUST STILL BE SAFE")
fresh_home()
d = account("exitcheck_blocked")
write_sidecar(d, ".delta.json", {"X-1": {"state": "open"}})
r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vm_precheck.py")],
                   capture_output=True, text=True, env=dict(os.environ, VM_PRECHECK_ANYWHERE="1"))   # standing in for the VM
check("non-zero exit when blocked - run for real as a subprocess, not just check()'s return value",
      r.returncode == 1, r.returncode)
check("BLOCKED is the actual last line of real stdout", r.stdout.strip().splitlines()[-1] == "BLOCKED - not restarting", r.stdout)

fresh_home()
r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vm_precheck.py")],
                   capture_output=True, text=True, env=dict(os.environ, VM_PRECHECK_ANYWHERE="1"))   # standing in for the VM
check("exit 0 and CLEAR TO RESTART printed for real, run as a subprocess, when nothing blocks",
      r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "CLEAR TO RESTART", (r.returncode, r.stdout))

print("13b. ONLY THE VM'S ANSWER COUNTS - RUN ANYWHERE ELSE IT REFUSES (5 Oct 2026: run on the Mac it read the")
print("     Mac's stale logs and printed CLEAR TO RESTART all day; the VM's open gold ticket was never listed)")
fresh_home()
env_off = {k: v for k, v in os.environ.items() if k != "VM_PRECHECK_ANYWHERE"}
r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vm_precheck.py")],
                   capture_output=True, text=True, env=env_off)
check("off the VM, with nothing open: exit 2 and BLOCKED, never a CLEAR from the wrong machine's logs",
      r.returncode == 2 and r.stdout.strip().splitlines()[-1] == "BLOCKED - not restarting"
      and "CLEAR TO RESTART" not in r.stdout, (r.returncode, r.stdout))
check("...and it prints the command that runs it ON the VM", "gcloud compute ssh nbs-signal-tool" in r.stdout
      and "vm_precheck.py" in r.stdout, r.stdout)
import socket as _socket
_real_host = _socket.gethostname
try:
    _socket.gethostname = lambda: "nbs-signal-tool"
    check("on the VM itself (its hostname): allowed", vp.on_the_vm() is True)
    _socket.gethostname = lambda: "Sureshs-MacBook-Pro.local"
    os.environ.pop("VM_PRECHECK_ANYWHERE", None)
    check("on a Mac: not", vp.on_the_vm() is False)
finally:
    _socket.gethostname = _real_host

print("14. real_positions()/open_paper_trades() DIRECTLY: MULTIPLE REAL POSITIONS IN ONE SIDECAR, ALL NAMED")
fresh_home()
d = account("multi")
write_sidecar(d, ".delta.json", {"M-1": {"state": "open"}, "M-2": {"state": "placing"}, "M-3": {"state": "closed"}})
reals = vp.real_positions(d)
ids = {tid for _, tid, _ in reals}
check("both non-closed positions are found, the closed one is not", ids == {"M-1", "M-2"}, ids)

print("15. open_paper_trades() EXCLUDES WHATEVER real_positions() ALREADY CLAIMED, GIVEN DIRECTLY")
fresh_home()
d = account("overlap")
write_trades(d, [op("O-1", "OPEN", index="BTC"), op("O-2", "OPEN", index="BTC")])
paper = vp.open_paper_trades(d, blocked_ids={"O-1"})
check("O-1 is withheld (already counted as real elsewhere), O-2 still reported", {p[0] for p in paper} == {"O-2"}, paper)

print("16. EXNESS (3 Oct 2026): AN OPEN POSITION BLOCKS; AN ORDER THAT NEVER WENT THROUGH DOES NOT")
fresh_home()
d = account("exness_acct")
write_sidecar(d, ".exness_live.json", {"BTC-9": {"state": "open", "index": "BTC"}})
ok, lines = vp.check()
check("an open Exness position blocks, named by its file and trade id", ok is False
      and any("trades.csv.exness_live.json" in l and "BTC-9" in l for l in lines), lines)
for state in ("attention", "some-new-state"):
    fresh_home()
    write_sidecar(account("x"), ".exness_live.json", {"G-1": {"state": state}})
    check(f"Exness state={state!r} blocks (fail-safe)", vp.check()[0] is False)
fresh_home()
write_sidecar(account("y"), ".exness_live.json", {"G-2": {"state": "failed"}, "G-3": {"state": "closed"}})
check("Exness 'failed' (never placed) and 'closed' do not block", vp.check()[0] is True)
fresh_home()
write_sidecar(account("z"), ".delta.json", {"D-1": {"state": "failed"}})
check("...while Delta's 'failed' still blocks, exactly as before", vp.check()[0] is False)

print("VM PRECHECK TEST PASSED" if not fails else f"VM PRECHECK TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
