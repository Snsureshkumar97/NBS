#!/usr/bin/env python3
"""journal_remove.py on made-up journals: only the day's LOSING closed trades go, from all three files, everything
else byte-for-byte, a backup of every changed file, nothing while the server runs."""
import io
import json
import os
import sys
import tempfile
import contextlib

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import journal_remove as jr
import trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

D = "2026-10-06"
HEAD = "trade_id,event,date,time_ist,index,status,pnl\n"
ROWS = [
    "WIN-1,OPEN,2026-10-06,09:57:32,SENSEX,OPEN,\n",
    "LOSS-1,OPEN,2026-10-06,12:41:24,BANKNIFTY,OPEN,\n",
    "WIN-1,CLOSE,2026-10-06,12:38:57,SENSEX,\"CLOSED — stop-loss hit (trailed to T1)\",3098.0\n",
    "LOSS-1,CLOSE,2026-10-06,14:38:18,BANKNIFTY,\"CLOSED — cleared manually, at a loss\",-5562.0\n",
    "OLD-1,OPEN,2026-10-05,09:21:59,BANKNIFTY,OPEN,\n",
    "OLD-1,CLOSE,2026-10-05,11:22:00,BANKNIFTY,CLOSED — stop-loss hit,-4327.5\n",
    "OPEN-1,OPEN,2026-10-06,15:01:00,NIFTY,OPEN,\n",
]

def account(email):
    path = trade_log.user_log_path(email, "nse_index")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(HEAD + "".join(ROWS))
    with open(path + ".live.fills.jsonl", "w") as f:
        for t in ("WIN-1", "LOSS-1", "OLD-1"):
            f.write(json.dumps({"trade_id": t, "gross_pnl": 1}) + "\n")
    with open(path + ".live.json", "w") as f:
        json.dump({"enabled": {"NIFTY": True}, "positions": {"WIN-1": {"state": "closed"}, "LOSS-1": {"state": "closed"},
                                                             "OLD-1": {"state": "closed"}}}, f)
    return path

p1, p2 = account("one@example.invalid"), account("two@example.invalid")
check("the day's LOSING closed trades are found - not a winner, not another day's loss, not one still open",
      jr.losing_ids(p1, D) == {"LOSS-1": -5562.0}, jr.losing_ids(p1, D))

jr.service_running = lambda: True
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = jr.main(["--date", D, "one@example.invalid"])
check("while the server runs: refuses and changes nothing", rc == 2 and "stop it first" in buf.getvalue()
      and "LOSS-1" in open(p1).read(), buf.getvalue())
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = jr.main(["--date", D, "--dry-run", "one@example.invalid"])
check("--dry-run (even with the server up): says what would go, changes nothing", rc == 0 and "would remove 1" in buf.getvalue()
      and "LOSS-1" in open(p1).read() and not any(n.endswith(".bak") for n in os.listdir(os.path.dirname(p1))), buf.getvalue())

jr.service_running = lambda: False
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = jr.main(["--date", D, "one@example.invalid", "two@example.invalid"])
out = buf.getvalue()
check("both accounts done, each named with what went", rc == 0 and out.count("removed 1 losing trade") == 2 and "-5,562.00" in out, out)
for p in (p1, p2):
    text = open(p).read()
    check("trades.csv: both of the loser's rows gone; every other line exactly as it was",
          text == HEAD + "".join(r for r in ROWS if not r.startswith("LOSS-1")), text)
    fills = [json.loads(l)["trade_id"] for l in open(p + ".live.fills.jsonl")]
    check("its real fill gone, the others kept", fills == ["WIN-1", "OLD-1"], fills)
    live = json.load(open(p + ".live.json"))
    check("its live position gone, the rest of the file kept", sorted(live["positions"]) == ["OLD-1", "WIN-1"]
          and live["enabled"] == {"NIFTY": True}, live)
    baks = sorted(n for n in os.listdir(os.path.dirname(p)) if ".pre-remove-" in n)
    check("a backup of each of the three files, holding the original", len(baks) == 3
          and "LOSS-1" in open(os.path.join(os.path.dirname(p), [b for b in baks if b.startswith("trades.csv.pre")][0])).read(), baks)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    jr.main(["--date", D, "one@example.invalid"])
check("run again: nothing left to remove, nothing changed", "removed 0 losing trade" in buf.getvalue()
      and len([n for n in os.listdir(os.path.dirname(p1)) if ".pre-remove-" in n]) == 3, buf.getvalue())

print()
print("JOURNAL REMOVE TEST PASSED" if not fails else f"JOURNAL REMOVE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
