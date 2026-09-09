#!/usr/bin/env python3
"""
target_study.py — what would ONE target have paid, instead of three?

Reads a trades.csv and asks the same question at several target levels. It is
honest about the one thing it cannot know: a trade that was closed early by a
signal flip never found out where it would have gone, so it is reported
separately rather than guessed at.

    python3 target_study.py trades.csv
"""
import csv
import sys
from collections import defaultdict


def f(row, key):
    v = (row.get(key) or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def load(path):
    opens, closes = {}, []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("event") == "OPEN":
                opens[row.get("trade_id")] = row
            elif row.get("event") == "CLOSE":
                closes.append(row)
    return opens, closes


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "trades.csv"
    opens, closes = load(path)

    rows = []
    for c in closes:
        o = opens.get(c.get("trade_id")) or c
        entry = f(c, "entry") or f(o, "entry")
        stop = f(c, "stop") or f(o, "stop")
        t1, t2, t3 = (f(c, "t1") or f(o, "t1"), f(c, "t2") or f(o, "t2"),
                      f(c, "t3") or f(o, "t3"))
        if not entry or not stop:
            continue
        rows.append({
            "status": c.get("status") or "",
            "entry": entry, "stop": stop, "t1": t1, "t2": t2, "t3": t3,
            "t1_hit": truthy(c.get("t1_hit")), "t2_hit": truthy(c.get("t2_hit")),
            "t3_hit": truthy(c.get("t3_hit")), "sl_hit": truthy(c.get("sl_hit")),
            "lot_size": f(c, "lot_size") or 0,
            "pnl": f(c, "pnl"),
        })

    print(f"\n{len(rows)} closed trades read from {path}\n")

    # ---- how far price actually travelled, in % of the entry premium -----
    # A trade that reached T1 travelled at least (t1-entry)/entry. That is the
    # only forward information a trade log carries: the log records the best
    # target touched, not the high-water mark, so anything ABOVE the best
    # target reached is unknowable from this file.
    print("HOW FAR EACH TRADE ACTUALLY GOT")
    print("  (the log records the best target touched — not the true high)")
    buckets = defaultdict(int)
    for r in rows:
        if r["t3_hit"]:
            buckets["reached T3 (+75%)"] += 1
        elif r["t2_hit"]:
            buckets["reached T2 (+40%) but not T3"] += 1
        elif r["t1_hit"]:
            buckets["reached T1 (+20%) but not T2"] += 1
        elif r["sl_hit"]:
            buckets["hit the stop first"] += 1
        else:
            buckets["neither — closed while in between"] += 1
    for k in ("reached T3 (+75%)", "reached T2 (+40%) but not T3",
              "reached T1 (+20%) but not T2", "hit the stop first",
              "neither — closed while in between"):
        n = buckets[k]
        print(f"  {k:36s} {n:4d}   {100.0*n/max(1,len(rows)):5.1f}%")

    # ---- what a single target would have scored --------------------------
    # Every trade is graded ONLY on what the log can prove: it reached the
    # target, or it hit the stop, or neither happened before it was closed.
    print("\nIF THERE HAD BEEN ONE TARGET INSTEAD OF THREE")
    print("  wins  = the log proves price reached that level")
    print("  loss  = the log proves the stop was hit")
    print("  ?     = closed before either happened — genuinely unknown\n")
    print(f"  {'single target':16s} {'win':>5s} {'loss':>5s} {'?':>5s}"
          f" {'win rate*':>10s} {'R per trade*':>13s}")

    LEVELS = [("+20% (old T1)", "t1", 0.20), ("+40% (old T2)", "t2", 0.40),
              ("+75% (old T3)", "t3", 0.75)]
    for label, key, _pct in LEVELS:
        win = loss = unknown = 0
        rs = []
        for r in rows:
            reached = r[{"t1": "t1_hit", "t2": "t2_hit", "t3": "t3_hit"}[key]]
            lvl = r[key]
            if lvl is None:
                continue
            risk = r["entry"] - r["stop"]
            if risk <= 0:
                continue
            reward_r = (lvl - r["entry"]) / risk
            if reached:
                win += 1
                rs.append(reward_r)
            elif r["sl_hit"]:
                loss += 1
                rs.append(-1.0)
            else:
                unknown += 1
        decided = win + loss
        wr = 100.0 * win / decided if decided else 0.0
        avg = sum(rs) / len(rs) if rs else 0.0
        print(f"  {label:16s} {win:5d} {loss:5d} {unknown:5d} "
              f"{wr:9.1f}% {avg:+12.2f}R")

    print("\n  * over DECIDED trades only. The '?' column is the honest hole:")
    print("    those trades were closed by a signal flip before the market")
    print("    answered the question, and no amount of arithmetic recovers it.")

    # ---- the reward:risk each level actually offered ----------------------
    print("\nWHAT EACH LEVEL WAS WORTH WHEN IT PAID")
    for label, key, _ in LEVELS:
        vals = []
        for r in rows:
            lvl = r[key]
            risk = r["entry"] - r["stop"]
            if lvl is None or risk <= 0:
                continue
            vals.append((lvl - r["entry"]) / risk)
        if vals:
            print(f"  {label:16s} average reward:risk "
                  f"{sum(vals)/len(vals):.2f} : 1")

    print("\nBREAK-EVEN WIN RATE EACH LEVEL NEEDS (before costs)")
    for label, key, _ in LEVELS:
        vals = []
        for r in rows:
            lvl = r[key]
            risk = r["entry"] - r["stop"]
            if lvl is None or risk <= 0:
                continue
            vals.append((lvl - r["entry"]) / risk)
        if vals:
            rr = sum(vals) / len(vals)
            need = 100.0 / (1.0 + rr)
            print(f"  {label:16s} needs {need:.1f}% of trades to win")


if __name__ == "__main__":
    main()
