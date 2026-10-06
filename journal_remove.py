#!/usr/bin/env python3
"""
journal_remove.py — remove one day's LOSING closed trades from accounts' journals
================================================================================
The operator's tool, run on the server with the server STOPPED (it keeps the day's trades in memory and would write
them back). The user, 6 Oct 2026: "remove the negative results today in indian market in both the accounts" - PAPER
tickets: checked against the live-order records, none of the day's losing trades had a Zerodha order or a real fill.

For each account it removes every trade CLOSED on --date with a negative P&L: its rows in trades.csv (open and close),
its real fill in trades.csv.live.fills.jsonl and its position in trades.csv.live.json. Every other line is kept
byte-for-byte. Each file it changes is copied first to <file>.pre-remove-<stamp>.bak, and it prints what it removed.

    .venv/bin/python journal_remove.py --date 2026-10-06 --market nse_index <email> [<email> ...]
    --dry-run      print what would go, change nothing
"""
import argparse
import csv
import datetime
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trade_log

SERVICE = "nbs-signal-tool"


def losing_ids(csv_path, date):
    """{trade_id: pnl} of trades CLOSED on `date` with a negative P&L."""
    out = {}
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("event") == "CLOSE" and r.get("date") == date:
                try:
                    pnl = float(r.get("pnl") or "")
                except ValueError:
                    continue
                if pnl < 0:
                    out[r["trade_id"]] = pnl
    return out


def _backup(path, stamp):
    dst = f"{path}.pre-remove-{stamp}.bak"
    shutil.copy2(path, dst)
    return dst


def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def remove(csv_path, date, dry_run=False, stamp=None):
    """Remove one account's losing trades of `date`. Returns ({trade_id: pnl}, [files changed])."""
    ids = losing_ids(csv_path, date)
    if not ids or dry_run:
        return ids, []
    stamp = stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    changed = []
    # trades.csv - parse each line only to read its trade_id; write the kept lines back exactly as they were
    with open(csv_path, newline="") as f:
        lines = f.read().splitlines(keepends=True)
    header = next(csv.reader([lines[0]]))
    col = header.index("trade_id")
    kept = [lines[0]] + [ln for ln in lines[1:] if not ln.strip() or next(csv.reader([ln]))[col] not in ids]
    _backup(csv_path, stamp)
    _write(csv_path, "".join(kept))
    changed.append(csv_path)
    fills = csv_path + ".live.fills.jsonl"
    if os.path.exists(fills):
        with open(fills) as f:
            lines = f.readlines()
        kept = [ln for ln in lines if not ln.strip() or json.loads(ln).get("trade_id") not in ids]
        if len(kept) != len(lines):
            _backup(fills, stamp)
            _write(fills, "".join(kept))
            changed.append(fills)
    live = csv_path + ".live.json"
    if os.path.exists(live):
        with open(live) as f:
            data = json.load(f)
        pos = data.get("positions") or {}
        if any(t in pos for t in ids):
            _backup(live, stamp)
            data["positions"] = {t: p for t, p in pos.items() if t not in ids}
            _write(live, json.dumps(data, indent=2))
            changed.append(live)
    return ids, changed


def service_running():
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0
    except FileNotFoundError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--market", default="nse_index")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("emails", nargs="+")
    a = ap.parse_args(argv)
    if not a.dry_run and service_running():
        print(f"{SERVICE} is running - stop it first (it holds the day's trades in memory and would write them back).")
        return 2
    for email in a.emails:
        path = trade_log.user_log_path(email, a.market)
        if not os.path.exists(path):
            print(f"{email}: no {a.market} journal at {path}")
            continue
        ids, changed = remove(path, a.date, dry_run=a.dry_run)
        what = "would remove" if a.dry_run else "removed"
        print(f"{email}: {what} {len(ids)} losing trade(s) closed {a.date}, total {sum(ids.values()):+,.2f}")
        for t, pnl in sorted(ids.items()):
            print(f"   {t}  {pnl:+,.2f}")
        for c in changed:
            print(f"   changed {os.path.basename(c)} (backup beside it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
