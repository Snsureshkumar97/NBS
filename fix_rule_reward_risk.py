#!/usr/bin/env python3
"""
fix_rule_reward_risk.py — correct the journal's reward:risk on the Exness rule trades logged before the fix
================================================================================
Until 4 Oct 2026 an Exness rule's reading (cfd_rules.apply) kept the options engine's room-to-run
numbers, so the trade log wrote the ENGINE's reward:risk for a rule trade - 17.51 on the first one,
whose own was 0.75 (target over stop). The user, 4 Oct 2026: "fix the journal reward risk".

The code now writes the rule's own; this corrects the rows already written, exactly as the fixed
code would have written them:
  * a trade is a rule trade when its OPEN row says confidence "Rule" and tracked_on "cfd";
  * each of its rows (OPEN and CLOSE) - every row stamps the reading AT THAT MOMENT (see
    market_bot's note on CLOSE rows) - gets reward_risk = the rule's target_r where the row's own
    reading was the rule saying buy/sell (confidence "Rule") - worked out from the row's OWN entry,
    stop and target (never today's config, which can change) - and nothing where it was quiet;
    reach_points (the engine's room to run) is emptied on both.
Nothing else in a file changes: every other line is kept byte for byte.

    python3 fix_rule_reward_risk.py [log_dir]            shows what it would change
    python3 fix_rule_reward_risk.py [log_dir] --apply    changes it (a .bak copy of each file first)

log_dir defaults to trade_log.log_dir(); every users/*/trades.csv under it is checked.
"""
import csv
import glob
import io
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trade_log


def _own_ratio(entry, stop, t3):
    """The trade's OWN reward:risk from its frozen levels (target over stop), as the code wrote it
    (the rule's target_r, 2 decimals) - never today's config: BTC's rule changed from 0.75 to 1.0 on
    4 Oct 2026 (the RSI-2 forward test), and a re-run must not rewrite the older trades with it."""
    try:
        e, s_, t = float(entry), float(stop), float(t3)
    except ValueError:
        return None
    return round(abs(t - e) / abs(e - s_), 2) if e != s_ else None


def plan_file(path):
    """[(line number, old line, new line, what changed)] for one trades.csv - nothing written."""
    with open(path, newline="") as f:
        lines = f.readlines()
    if not lines:
        return []
    header = next(csv.reader([lines[0]]))
    try:
        i_id, i_ev, i_ix, i_on, i_conf = (header.index(k) for k in ("trade_id", "event", "index", "tracked_on", "confidence"))
        i_rr, i_reach = header.index("reward_risk"), header.index("reach_points")
        i_en, i_sl, i_t3 = header.index("entry"), header.index("stop"), header.index("t3")
    except ValueError:
        return []
    parsed = []
    for n, line in enumerate(lines[1:], start=1):
        try:
            row = next(csv.reader([line]))
        except (csv.Error, StopIteration):
            row = None
        parsed.append((n, line, row if row and len(row) == len(header) else None))
    rule_ids = {row[i_id] for _, _, row in parsed
                if row and row[i_ev] == "OPEN" and row[i_conf] == "Rule" and row[i_on] == "cfd"}
    out = []
    for n, line, row in parsed:
        if not row or row[i_id] not in rule_ids:
            continue
        tr = _own_ratio(row[i_en], row[i_sl], row[i_t3])
        if tr is None:
            continue
        new = list(row)
        new[i_rr] = str(tr) if row[i_conf] == "Rule" else ""
        new[i_reach] = ""
        if new == row:
            continue
        ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        buf = io.StringIO()
        csv.writer(buf, lineterminator=ending).writerow(new)
        out.append((n, line, buf.getvalue(), f"{row[i_id]} {row[i_ev]}  reward_risk {row[i_rr] or '(empty)'} -> "
                    f"{new[i_rr] or '(empty)'}, reach_points {row[i_reach] or '(empty)'} -> (empty)"))
    return out


def apply_file(path, changes):
    """Write the changes: a .bak copy first, then a temp file swapped in - and only if the file is still
    the one that was read (a ticket appending a row meanwhile would otherwise be lost)."""
    st = os.stat(path)
    with open(path, newline="") as f:
        lines = f.readlines()
    for n, old, new, _ in changes:
        if lines[n] != old:
            return "changed since it was read - left alone, run it again"
        lines[n] = new
    bak = f"{path}.bak-rr-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, bak)
    tmp = path + ".fixrr"
    with open(tmp, "w", newline="") as f:
        f.writelines(lines)
    shutil.copymode(path, tmp)
    now = os.stat(path)
    if (now.st_size, now.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
        os.remove(tmp)
        return "changed while writing - left alone, run it again"
    os.replace(tmp, path)
    return f"fixed (backup {os.path.basename(bak)})"


def main(argv):
    apply = "--apply" in argv
    args = [a for a in argv if a != "--apply"]
    root = os.path.expanduser(args[0]) if args else trade_log.log_dir()
    files = sorted(glob.glob(os.path.join(root, "users", "*", trade_log.CSV_NAME)))
    print(f"{len(files)} trade logs under {root}")
    total = 0
    for path in files:
        changes = plan_file(path)
        if not changes:
            continue
        total += len(changes)
        key = os.path.basename(os.path.dirname(path))[:6]
        for n, _, _, what in changes:
            print(f"  {key} line {n}: {what}")
        if apply:
            print(f"  {key}: {apply_file(path, changes)}")
    print(f"{total} row(s) {'corrected' if apply else 'to correct - run again with --apply'}" if total
          else "nothing to correct")


if __name__ == "__main__":
    main(sys.argv[1:])
